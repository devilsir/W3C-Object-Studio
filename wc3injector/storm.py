from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import hashlib
import os
import re
import tempfile
import threading

from .errors import StormLibError
from .debuglog import configure_logging

LOGGER, _SESSION_LOG = configure_logging()

MPQ_FILE_COMPRESS = 0x00000200
MPQ_FILE_REPLACEEXISTING = 0x80000000
MPQ_COMPRESSION_ZLIB = 0x02
MPQ_COMPRESSION_NEXT_SAME = 0xFFFFFFFF
SFILE_OPEN_FROM_MPQ = 0x00000000
MPQ_OPEN_READ_ONLY = 0x00000100

# StormLib can reject a second open of the same archive on Windows even when
# both callers only want to read. The GUI starts icon/model extraction on
# worker threads, so serialize native archive handles per physical map path.
# This lock is process-local: it prevents our own preview workers/save worker
# from racing each other without blocking unrelated maps.
_ARCHIVE_LOCKS_GUARD = threading.Lock()
_ARCHIVE_LOCKS: dict[str, threading.Lock] = {}

def _archive_lock_for(path: Path) -> threading.Lock:
    try:
        key = str(path.resolve())
    except OSError:
        key = str(path.absolute())
    if os.name == "nt":
        key = key.casefold()
    with _ARCHIVE_LOCKS_GUARD:
        lock = _ARCHIVE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _ARCHIVE_LOCKS[key] = lock
        return lock
SFILE_INVALID_SIZE = 0xFFFFFFFF

# StormLib is a C++ API and its success-returning functions use native C++
# `bool`, not Win32 BOOL. On 64-bit Windows, binding these as a 32-bit BOOL
# can turn a false return into a random non-zero value because only the low
# byte is defined by the ABI. Use c_bool for exact return-width semantics.
STORM_BOOL = ctypes.c_bool

_WINDOWS_FORBIDDEN_BYTES = frozenset(b'<>:"/\\|?*')
_WINDOWS_RESERVED_STEMS = {
    'con', 'prn', 'aux', 'nul',
    *{f'com{i}' for i in range(1, 10)},
    *{f'lpt{i}' for i in range(1, 10)},
}


def archive_member_bytes(archive_path: str | bytes) -> bytes:
    """Return one MPQ member name without changing its original bytes.

    Warcraft maps in the wild are not guaranteed to store import names as
    UTF-8.  ``war3map.imp`` is therefore decoded with ``surrogateescape`` by
    :mod:`wc3injector.imports`; this function is the matching inverse used at
    the StormLib boundary.  Using plain ``encode('utf-8')`` here corrupts (or
    rejects) legacy/mixed-encoding member names even though the MPQ itself is
    perfectly valid.
    """
    if isinstance(archive_path, bytes):
        raw = archive_path
    else:
        raw = str(archive_path).encode('utf-8', errors='surrogateescape')
    return raw.replace(b'/', b'\\')


def archive_path_for_log(archive_path: str | bytes) -> str:
    """Lossless-enough, console-safe representation of an MPQ path."""
    return archive_member_bytes(archive_path).decode('utf-8', errors='backslashreplace')


def _safe_local_component_bytes(raw: bytes, fallback: str = 'asset.bin') -> str:
    raw = bytes(raw or b'')
    if not raw:
        return fallback

    # Keep the normal case byte-for-byte readable.  Anything non-ASCII or
    # illegal on Win32 gets a deterministic short name.  This is only for our
    # temporary/export filesystem; the MPQ path itself is never renamed.
    ascii_ok = (
        len(raw) <= 180
        and all(32 <= b < 127 for b in raw)
        and not any(b in _WINDOWS_FORBIDDEN_BYTES for b in raw)
        and raw[-1:] not in {b' ', b'.'}
    )
    if ascii_ok:
        text = raw.decode('ascii')
        stem = text.split('.', 1)[0].casefold()
        if stem not in _WINDOWS_RESERVED_STEMS:
            return text

    ext = ''
    if b'.' in raw:
        tail = raw.rsplit(b'.', 1)[1]
        if 1 <= len(tail) <= 12 and all(
            (48 <= b <= 57) or (65 <= b <= 90) or (97 <= b <= 122)
            for b in tail
        ):
            ext = '.' + tail.decode('ascii')
    digest = hashlib.sha1(raw).hexdigest()[:16]
    return f'wc3asset_{digest}{ext}'


def safe_archive_basename(archive_path: str | bytes, fallback: str = 'asset.bin') -> str:
    """Create a Win32-safe temporary filename for an MPQ/CASC member."""
    raw = archive_member_bytes(archive_path)
    # CASC archive chains may contain ':' before the actual Warcraft path.
    raw = raw.rsplit(b':', 1)[-1]
    base = raw.rsplit(b'\\', 1)[-1]
    return _safe_local_component_bytes(base, fallback=fallback)


def safe_archive_relpath(archive_path: str | bytes, fallback: str = 'asset.bin') -> str:
    """Create a ZIP/filesystem-safe relative path while preserving safe ASCII."""
    raw = archive_member_bytes(archive_path).rsplit(b':', 1)[-1]
    parts = [p for p in raw.split(b'\\') if p not in {b'', b'.', b'..'}]
    if not parts:
        return fallback
    return '/'.join(_safe_local_component_bytes(p, fallback='asset') for p in parts)


def locate_stormlib(explicit: str | None = None) -> Path:
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.getenv("STORMLIB_DLL")
    if env:
        candidates.append(Path(env))
    root = Path(__file__).resolve().parent.parent
    candidates += [root / "bin" / "StormLib.dll", root / "StormLib.dll", Path.cwd() / "StormLib.dll"]
    LOGGER.debug("StormLib locate candidates: %s", [str(x) for x in candidates])
    for p in candidates:
        if p.exists():
            found=p.resolve()
            LOGGER.info("StormLib located: %s (%d bytes)", found, found.stat().st_size)
            return found
    raise StormLibError("StormLib.dll não encontrado. Rode setup_stormlib.py ou coloque a DLL em bin/StormLib.dll")


def _win_error_text(code: int) -> str:
    if os.name != "nt" or not code:
        return ""
    try:
        return ctypes.FormatError(code).strip()
    except Exception:
        return ""


class StormArchive:
    """Small, conservative StormLib wrapper.

    Important Windows detail: StormLib can be built either ANSI or UNICODE and
    both variants export the *same* SFileOpenArchive/SFileAddFileEx symbol.
    There is no A/W suffix to inspect.  The official release packages and
    third-party builds are therefore not guaranteed to agree on LPCTSTR.

    To avoid ERROR_INVALID_NAME (123) and broken non-ASCII paths, archive open
    probes both TCHAR modes once, remembers the working mode, and uses that
    exact mode for every subsequent on-disk path passed to StormLib.  Names
    *inside* an MPQ remain byte strings, as required by the StormLib API.
    """

    def __init__(self, map_path: str | Path, dll_path: str | None = None, *, read_only: bool = False):
        self.map_path = Path(map_path)
        self.dll_path = locate_stormlib(dll_path)
        self.dll = (
            ctypes.WinDLL(str(self.dll_path), use_last_error=True)
            if os.name == "nt"
            else ctypes.CDLL(str(self.dll_path), use_errno=True)
        )
        self.h = ctypes.c_void_p()
        self._tchar_mode: str | None = None
        self.read_only = bool(read_only)
        self._path_lock: threading.Lock | None = None
        self._path_lock_acquired = False
        LOGGER.debug("StormArchive init map=%s dll=%s os=%s read_only=%s", self.map_path, self.dll_path, os.name, self.read_only)
        self._bind()

    def _bind(self):
        d = self.dll

        # TCHAR differs between ANSI and UNICODE StormLib builds, but the
        # exported function name does not. Bind that argument as an opaque
        # pointer and supply a correctly typed buffer at call time.
        d.SFileOpenArchive.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        d.SFileOpenArchive.restype = STORM_BOOL
        d.SFileCloseArchive.argtypes = [ctypes.c_void_p]
        d.SFileCloseArchive.restype = STORM_BOOL
        d.SFileHasFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        d.SFileHasFile.restype = STORM_BOOL
        d.SFileOpenFileEx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        d.SFileOpenFileEx.restype = STORM_BOOL
        d.SFileGetFileSize.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
        d.SFileGetFileSize.restype = wintypes.DWORD
        d.SFileReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        d.SFileReadFile.restype = STORM_BOOL
        d.SFileCloseFile.argtypes = [ctypes.c_void_p]
        d.SFileCloseFile.restype = STORM_BOOL

        # local/source path is TCHAR; archive path is always narrow char*.
        d.SFileAddFileEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        d.SFileAddFileEx.restype = STORM_BOOL
        if hasattr(d, "SFileGetMaxFileCount"):
            d.SFileGetMaxFileCount.argtypes = [ctypes.c_void_p]
            d.SFileGetMaxFileCount.restype = wintypes.DWORD
        if hasattr(d, "SFileSetMaxFileCount"):
            d.SFileSetMaxFileCount.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            d.SFileSetMaxFileCount.restype = STORM_BOOL
        if hasattr(d, "SFileCompactArchive"):
            d.SFileCompactArchive.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            d.SFileCompactArchive.restype = STORM_BOOL

    def _last_error(self) -> int:
        return ctypes.get_last_error() if os.name == "nt" else ctypes.get_errno()

    def _err(self, action: str, code: int | None = None):
        code = self._last_error() if code is None else int(code)
        detail = _win_error_text(code)
        extra = f": {detail}" if detail else ""
        LOGGER.error("StormLib error action=%s code=%s detail=%s map=%s", action, code, detail, self.map_path)
        raise StormLibError(f"{action} falhou (erro {code}{extra})")

    @staticmethod
    def _archive_name(archive_path: str | bytes) -> bytes:
        # MPQ member names are char* in StormLib even in UNICODE builds.
        # Preserve legacy/mixed byte encodings from war3map.imp exactly.
        return archive_member_bytes(archive_path)

    @staticmethod
    def _native_path_pointer(path: str | Path, mode: str):
        text = str(Path(path))
        if mode == "wide":
            holder = ctypes.c_wchar_p(text)
        else:
            if os.name == "nt":
                try:
                    raw = text.encode("mbcs", errors="strict")
                except UnicodeEncodeError as exc:
                    raise StormLibError(
                        "o caminho contém caracteres que a build ANSI da StormLib não consegue representar; "
                        "mova o arquivo para um caminho simples ou use a build Unicode"
                    ) from exc
            else:
                raw = os.fsencode(text)
            holder = ctypes.c_char_p(raw)
        return holder, ctypes.cast(holder, ctypes.c_void_p)

    def _try_open_mode(self, mode: str) -> tuple[bool, int]:
        holder, ptr = self._native_path_pointer(self.map_path, mode)
        # Keep holder alive until the native call returns.
        _ = holder
        if os.name == "nt":
            ctypes.set_last_error(0)
        else:
            ctypes.set_errno(0)
        self.h = ctypes.c_void_p()
        LOGGER.debug("SFileOpenArchive probe mode=%s map=%s", mode, self.map_path)
        open_flags = MPQ_OPEN_READ_ONLY if self.read_only else 0
        ok = bool(self.dll.SFileOpenArchive(ptr, 0, open_flags, ctypes.byref(self.h)))
        # A successful open must also return a non-null archive handle. This
        # makes a bad third-party ABI binding fail closed rather than letting a
        # null handle leak into later calls.
        ok = ok and bool(self.h.value)
        code=self._last_error()
        LOGGER.debug("SFileOpenArchive result mode=%s flags=0x%X ok=%s handle=%s error=%s %s", mode, (MPQ_OPEN_READ_ONLY if self.read_only else 0), ok, self.h.value, code, _win_error_text(code))
        return ok, code

    def __enter__(self):
        LOGGER.info("Opening MPQ: %s", self.map_path)
        if not self.map_path.exists():
            raise StormLibError(f"mapa não encontrado: {self.map_path}")
        if not self.map_path.is_file():
            raise StormLibError(f"o caminho não é um arquivo: {self.map_path}")

        # Resolve only after validating; this also normalizes relative paths.
        self.map_path = self.map_path.resolve()

        # IMPORTANT: icon and model preview are loaded from worker threads.
        # Opening the same MPQ concurrently through StormLib can yield Win32
        # ERROR_SHARING_VIOLATION (32), even though both operations are reads.
        # Hold one process-local lock for the entire native archive lifetime.
        self._path_lock = _archive_lock_for(self.map_path)
        LOGGER.debug("Waiting for MPQ path lock: %s", self.map_path)
        self._path_lock.acquire()
        self._path_lock_acquired = True
        LOGGER.debug("Acquired MPQ path lock: %s", self.map_path)

        try:
            return self._open_locked()
        except Exception:
            self._release_path_lock()
            raise

    def _open_locked(self):
        if os.name == "nt":
            # We cannot know whether a StormLib.dll was compiled with
            # _UNICODE just by looking at its exported symbol. Probe both.
            attempts: list[tuple[str, int]] = []
            for mode in ("wide", "ansi"):
                try:
                    ok, code = self._try_open_mode(mode)
                except StormLibError:
                    # ANSI conversion itself can fail for a Unicode-only path;
                    # in that case just continue with the other probe result.
                    continue
                attempts.append((mode, code))
                if ok:
                    self._tchar_mode = mode
                    LOGGER.info("MPQ opened successfully mode=%s handle=%s map=%s", mode, self.h.value, self.map_path)
                    return self

            details = ", ".join(
                f"{mode}={code}{(' (' + _win_error_text(code) + ')') if _win_error_text(code) else ''}"
                for mode, code in attempts
            ) or "sem código de erro disponível"
            raise StormLibError(
                "SFileOpenArchive não conseguiu abrir o mapa. "
                f"Tentativas de caminho StormLib: {details}. "
                "Confirme que o arquivo é um .w3m/.w3x válido e não está sendo passado por uma build StormLib incompatível."
            )

        ok, code = self._try_open_mode("ansi")
        if not ok:
            self._err("SFileOpenArchive", code)
        self._tchar_mode = "ansi"
        LOGGER.info("MPQ opened successfully mode=ansi handle=%s map=%s", self.h.value, self.map_path)
        return self

    def _release_path_lock(self):
        if self._path_lock_acquired and self._path_lock is not None:
            self._path_lock_acquired = False
            try:
                self._path_lock.release()
                LOGGER.debug("Released MPQ path lock: %s", self.map_path)
            finally:
                self._path_lock = None

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.h:
                LOGGER.debug("Closing MPQ handle=%s map=%s exc=%s", self.h.value, self.map_path, exc_type.__name__ if exc_type else None)
                self.dll.SFileCloseArchive(self.h)
                self.h = ctypes.c_void_p()
        finally:
            self._release_path_lock()

    def has(self, archive_path: str | bytes) -> bool:
        # Open-and-close is intentionally used instead of trusting only
        # SFileHasFile's boolean. Besides the exact c_bool binding, requiring a
        # non-null file handle gives us a second ABI guard: a missing file can
        # never become a false positive just because a foreign DLL returned a
        # malformed boolean value.
        fh = ctypes.c_void_p()
        if os.name == "nt":
            ctypes.set_last_error(0)
        else:
            ctypes.set_errno(0)
        opened = bool(self.dll.SFileOpenFileEx(
            self.h, self._archive_name(archive_path), SFILE_OPEN_FROM_MPQ, ctypes.byref(fh)
        ))
        if not opened or not fh.value:
            LOGGER.debug("MPQ has? %s -> False (opened=%s handle=%s err=%s)", archive_path_for_log(archive_path), opened, fh.value, self._last_error())
            return False
        self.dll.SFileCloseFile(fh)
        LOGGER.debug("MPQ has? %s -> True", archive_path_for_log(archive_path))
        return True

    def read(self, archive_path: str | bytes) -> bytes:
        label = archive_path_for_log(archive_path)
        LOGGER.debug("MPQ read start: %s", label)
        name = self._archive_name(archive_path)
        fh = ctypes.c_void_p()
        if os.name == "nt":
            ctypes.set_last_error(0)
        else:
            ctypes.set_errno(0)
        opened = bool(self.dll.SFileOpenFileEx(self.h, name, SFILE_OPEN_FROM_MPQ, ctypes.byref(fh)))
        if not opened or not fh.value:
            self._err(f"abrir {label}")
        try:
            hi = wintypes.DWORD(0)
            # SFileGetFileSize uses 0xFFFFFFFF as the error sentinel. Clear the
            # thread error first so a stale Windows error cannot be reported as
            # if it came from this size query.
            if os.name == "nt":
                ctypes.set_last_error(0)
            else:
                ctypes.set_errno(0)
            size = int(self.dll.SFileGetFileSize(fh, ctypes.byref(hi)))
            size_error = self._last_error()
            if size == SFILE_INVALID_SIZE and size_error:
                self._err(f"tamanho de {label}", size_error)
            total = (int(hi.value) << 32) | size
            if total > 2**31:
                raise StormLibError(f"arquivo interno grande demais para leitura segura: {label}")
            LOGGER.debug("MPQ read size: %s = %d bytes (hi=%d low=%d err=%d)", label, total, hi.value, size, size_error)
            if total == 0:
                return b""
            buf = ctypes.create_string_buffer(total)
            got = wintypes.DWORD(0)
            if os.name == "nt":
                ctypes.set_last_error(0)
            else:
                ctypes.set_errno(0)
            if not self.dll.SFileReadFile(fh, buf, total, ctypes.byref(got), None):
                self._err(f"ler {label}")
            out=bytes(buf.raw[:got.value])
            LOGGER.debug("MPQ read done: %s got=%d", label, len(out))
            return out
        finally:
            if fh.value:
                self.dll.SFileCloseFile(fh)

    def add_file(self, local_path: str | Path, archive_path: str | bytes, compress: bool = True):
        if self.read_only:
            raise StormLibError("tentativa de gravar em um MPQ aberto em modo somente leitura")
        label = archive_path_for_log(archive_path)
        LOGGER.info("MPQ add/replace: local=%s archive=%s compress=%s", local_path, label, compress)
        flags = MPQ_FILE_REPLACEEXISTING | (MPQ_FILE_COMPRESS if compress else 0)
        compression = MPQ_COMPRESSION_ZLIB if compress else 0
        mode = self._tchar_mode or ("wide" if os.name == "nt" else "ansi")
        holder, source_ptr = self._native_path_pointer(Path(local_path).resolve(), mode)
        _ = holder
        archive_name = self._archive_name(archive_path)

        def _try_add() -> bool:
            if os.name == "nt":
                ctypes.set_last_error(0)
            return bool(self.dll.SFileAddFileEx(
                self.h,
                source_ptr,
                archive_name,
                flags,
                compression,
                MPQ_COMPRESSION_NEXT_SAME,
            ))

        if _try_add():
            LOGGER.debug("MPQ add succeeded: %s", label)
            return

        # Full MPQ hash tables are common in old/custom maps. Grow only after
        # an actual add failure, and only on the disposable transaction copy.
        if hasattr(self.dll, "SFileGetMaxFileCount") and hasattr(self.dll, "SFileSetMaxFileCount"):
            current = int(self.dll.SFileGetMaxFileCount(self.h))
            if 0 < current < 0x00080000:
                wanted = min(0x00080000, max(current * 2, current + 1024))
                LOGGER.warning("MPQ add failed; growing max file count current=%s wanted=%s", current, wanted)
                if self.dll.SFileSetMaxFileCount(self.h, wanted) and _try_add():
                    LOGGER.info("MPQ add succeeded after hash table growth: %s", label)
                    return
        self._err(f"inserir {label}")

    def write(self, archive_path: str | bytes, data: bytes, compress: bool = True):
        LOGGER.info("MPQ write bytes: %s (%d bytes) compress=%s", archive_path_for_log(archive_path), len(data), compress)
        # Use an ASCII-only temp directory/file name. This also keeps source
        # paths friendly to ANSI StormLib builds during writes.
        fd, tmp = tempfile.mkstemp(prefix="wc3inj_", suffix=".bin")
        os.close(fd)
        try:
            Path(tmp).write_bytes(data)
            self.add_file(tmp, archive_path, compress=compress)
        finally:
            try:
                Path(tmp).unlink()
            except OSError:
                pass

    def compact(self):
        if self.read_only:
            raise StormLibError("tentativa de compactar um MPQ aberto em modo somente leitura")
        LOGGER.info("MPQ compact requested")
        if hasattr(self.dll, "SFileCompactArchive"):
            if not self.dll.SFileCompactArchive(self.h, None, False):
                self._err("compactar MPQ")
