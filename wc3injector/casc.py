from __future__ import annotations

import atexit
import ctypes
import json
import logging
import os
import threading
import urllib.request
from pathlib import Path
from typing import Iterable

LOGGER = logging.getLogger("wc3studio")

CASC_LOCALE_ALL = 0xFFFFFFFF
CASC_LOCALE_NONE = 0x00000000
CASC_LOCALE_ENUS = 0x00000002
CASC_LOCALE_PTBR = 0x00004000
CASC_OPEN_BY_NAME = 0x00000000

_LOCK = threading.RLock()
_SHARED_LOCK = threading.RLock()
_SHARED_CASC = None
_SHARED_CASC_KEY = ""



def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_dll_path() -> Path:
    return _root() / "casc_lib" / "CascLib.dll"

CASCLIB_DOWNLOAD_URL = "https://raw.githubusercontent.com/Darithos/W3ModelViewer/main/native/CascLib.dll"

def ensure_casclib(timeout: float = 20.0) -> Path:
    """Ensure the x64 CascLib DLL used for local Reforged assets exists.

    The binary is the CascLib build bundled by the open-source W3ModelViewer
    project; source remains Zezula/CascLib (MIT). It is downloaded on the
    user's machine and is never written into a Warcraft map.
    """
    path=default_dll_path()
    if path.exists() and path.stat().st_size > 100_000:
        return path
    if os.name != "nt":
        return path
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix('.dll.part')
    LOGGER.info("Downloading CascLib for local Warcraft III CASC: %s",CASCLIB_DOWNLOAD_URL)
    req=urllib.request.Request(CASCLIB_DOWNLOAD_URL,headers={"User-Agent":"WC3-Object-Studio/3.2"})
    with urllib.request.urlopen(req,timeout=timeout) as resp:
        data=resp.read()
    if len(data)<100_000 or data[:2] != b'MZ':
        raise CascError(f"download CascLib inválido ({len(data)} bytes)")
    tmp.write_bytes(data); tmp.replace(path)
    LOGGER.info("CascLib ready: %s (%d bytes)",path,len(data))
    return path


def _looks_like_wc3(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
        return any((path / name).exists() for name in (".build.info", "Data", "_retail_", "Warcraft III.exe", "x86_64"))
    except Exception:
        return False


def _remember_file() -> Path:
    return _root() / "native_data" / "wc3_install.json"


def remember_warcraft_install(path: str | Path) -> None:
    p = Path(path).expanduser().resolve()
    if not _looks_like_wc3(p):
        raise ValueError(f"A pasta não parece uma instalação do Warcraft III: {p}")
    f = _remember_file(); f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"path": str(p)}, ensure_ascii=False, indent=2), encoding="utf-8")
    # If the user points the editor at another Warcraft install, discard the
    # process-wide CASC handle so the next read opens the new storage.
    reset = globals().get("reset_shared_casc")
    if callable(reset):
        reset()


def _registry_candidates() -> list[Path]:
    if os.name != "nt":
        return []
    out: list[Path] = []
    try:
        import winreg
        roots = [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]
        keys = [
            r"SOFTWARE\WOW6432Node\Blizzard Entertainment\Warcraft III",
            r"SOFTWARE\Blizzard Entertainment\Warcraft III",
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Warcraft III",
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Warcraft III",
        ]
        for root in roots:
            for key_name in keys:
                try:
                    with winreg.OpenKey(root, key_name) as k:
                        for value_name in ("InstallPath", "InstallLocation", "Path"):
                            try:
                                value, _ = winreg.QueryValueEx(k, value_name)
                                if value:
                                    out.append(Path(os.path.expandvars(str(value))))
                            except OSError:
                                pass
                except OSError:
                    pass

        # Some Battle.net installs only expose an uninstall record with a dynamic key.
        for root in roots:
            for branch in (
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
            ):
                try:
                    with winreg.OpenKey(root, branch) as parent:
                        count, _, _ = winreg.QueryInfoKey(parent)
                        for i in range(count):
                            try:
                                sub = winreg.EnumKey(parent, i)
                                with winreg.OpenKey(parent, sub) as k:
                                    name = ""
                                    try: name = str(winreg.QueryValueEx(k, "DisplayName")[0])
                                    except OSError: pass
                                    if "warcraft iii" not in name.casefold():
                                        continue
                                    for value_name in ("InstallLocation", "InstallPath"):
                                        try:
                                            value, _ = winreg.QueryValueEx(k, value_name)
                                            if value: out.append(Path(os.path.expandvars(str(value))))
                                        except OSError: pass
                            except OSError:
                                continue
                except OSError:
                    pass
    except Exception:
        LOGGER.debug("Registry WC3 detection failed", exc_info=True)
    return out


def detect_warcraft_install() -> Path | None:
    candidates: list[Path] = []
    for env in ("WC3_INSTALL_PATH", "WARCRAFT3_PATH", "WARCRAFT_III_PATH"):
        if os.environ.get(env):
            candidates.append(Path(os.environ[env]))
    remembered = _remember_file()
    if remembered.exists():
        try:
            value = json.loads(remembered.read_text(encoding="utf-8")).get("path")
            if value: candidates.append(Path(value))
        except Exception:
            pass
    candidates.extend(_registry_candidates())
    if os.name == "nt":
        for base in filter(None, (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("PUBLIC"))):
            candidates.append(Path(base) / "Warcraft III")
        # Common custom Battle.net locations.
        for drive in ("C:", "D:", "E:", "F:"):
            candidates.extend([
                Path(drive) / "Games" / "Warcraft III",
                Path(drive) / "Battle.net" / "Warcraft III",
                Path(drive) / "Warcraft III",
            ])
    seen=set()
    for p in candidates:
        try:
            p=p.expanduser().resolve()
        except Exception:
            continue
        key=str(p).casefold()
        if key in seen: continue
        seen.add(key)
        if _looks_like_wc3(p):
            LOGGER.info("Detected Warcraft III CASC install: %s", p)
            return p
    return None


def casc_path_candidates(ref: str, near: str | None = None, preference: str = "hd") -> list[str]:
    """Return likely CASC names for a Warcraft III virtual asset path.

    Warcraft III patch 3.0 exposes three stock art sets in CASC:
      * Classic / SD:       ``war3.w3mod:<path>``
      * Reforged / HD:      ``war3.w3mod:_hd.w3mod:<path>``
      * Definitive / DE:    ``war3.w3mod:_de.w3mod:<path>``

    The selected art mode controls model/texture resolution only. Explicit data
    files (SLK/TXT/etc.) remain authoritative so a graphics toggle can never
    redirect Object Editor metadata to another namespace.
    """
    raw=(ref or "").strip().strip('"').replace("/", "\\")
    near_raw=(near or "").strip().strip('"').replace("/", "\\")
    while "\\\\" in raw:
        raw=raw.replace("\\\\", "\\")
    while "\\\\" in near_raw:
        near_raw=near_raw.replace("\\\\", "\\")
    while raw.startswith("\\"):
        raw=raw[1:]
    while near_raw.startswith("\\"):
        near_raw=near_raw[1:]
    if not raw:
        return []

    out=[]
    seen=set()
    def add(value: str):
        value=str(value or "")
        key=value.casefold()
        if value and key not in seen:
            seen.add(key)
            out.append(value)

    def add_relative_candidate(base: str, child: str):
        if not base or not child:
            return
        base=base.replace('/', '\\')
        child=child.replace('/', '\\')
        if ':' in base:
            ns, path = base.rsplit(':', 1)
            parent = path.rsplit('\\', 1)[0] if '\\' in path else path
            add(ns + ':' + (parent + '\\' + child if parent else child))
        else:
            parent = base.rsplit('\\', 1)[0] if '\\' in base else base
            add(parent + '\\' + child if parent else child)

    # Preserve exact model-relative texture lookup before global fallbacks.
    # When the source model itself came from _de.w3mod, this naturally probes
    # the DE-relative texture first.
    if near_raw:
        add_relative_candidate(near_raw, raw)
        if '\\' in raw:
            add_relative_candidate(near_raw, raw.split('\\', 1)[1])

    pref=(preference or "hd").casefold()
    classic_pref=pref in {"classic", "sd", "spell"}
    de_pref=pref in {"de", "definitive", "definitive edition"}
    hd_pref=not classic_pref and not de_pref

    low=raw.casefold()
    de_prefix='war3.w3mod:_de.w3mod:'
    hd_prefix='war3.w3mod:_hd.w3mod:'
    classic_compat_prefix='war3.w3mod:_classic.w3mod:'
    base_prefix='war3.w3mod:'
    sd_prefix='war3sd.w3mod:'

    # Only art assets may be transparently rebased between graphics trees.
    # Never reinterpret UnitData.slk, profile txt files, metadata, etc.
    logical_ext=Path(raw.rsplit(':', 1)[-1]).suffix.casefold()
    art_asset=logical_ext in {'.mdx', '.mdl', '.blp', '.dds', '.tga', '.tif', '.png', '.jpg', '.jpeg'}

    def add_mode_chain(logical: str, mode: str):
        if mode == 'de':
            # The DE tree is intentionally a third art set. When a particular
            # DE asset is absent, mirror Warcraft-style compatibility by falling
            # back to Reforged HD, then Classic/SD.
            add(de_prefix + logical)
            add(hd_prefix + logical)
            add(base_prefix + logical)
            add(sd_prefix + logical)
            add(classic_compat_prefix + logical)
        elif mode == 'classic':
            add(base_prefix + logical)
            add(sd_prefix + logical)
            add(classic_compat_prefix + logical)
            add(hd_prefix + logical)
            add(de_prefix + logical)
        else:
            add(hd_prefix + logical)
            add(base_prefix + logical)
            add(sd_prefix + logical)
            add(classic_compat_prefix + logical)
            # Last-resort only: this keeps brand-new DE-only objects previewable
            # without making DE silently replace the selected Reforged art.
            add(de_prefix + logical)

    mode='de' if de_pref else ('classic' if classic_pref else 'hd')

    if low.startswith(de_prefix):
        logical=raw[len(de_prefix):]
        if art_asset and not de_pref:
            add_mode_chain(logical, mode)
            add(raw)
        else:
            add(raw)
            if art_asset and de_pref:
                add(hd_prefix + logical); add(base_prefix + logical)
    elif low.startswith(hd_prefix):
        logical=raw[len(hd_prefix):]
        if art_asset and (classic_pref or de_pref):
            add_mode_chain(logical, mode)
            add(raw)
        else:
            add(raw)
            if art_asset and hd_pref:
                add(base_prefix + logical); add(de_prefix + logical)
    elif low.startswith((base_prefix, sd_prefix, classic_compat_prefix)):
        # Explicit metadata/data namespaces remain exact. Explicit stock art is
        # allowed to follow the selected DE/HD mode because native profile rows
        # frequently store a base-tree spelling even when alternate art exists.
        if art_asset and (de_pref or hd_pref):
            if low.startswith(base_prefix):
                logical=raw[len(base_prefix):]
            elif low.startswith(sd_prefix):
                logical=raw[len(sd_prefix):]
            else:
                logical=raw[len(classic_compat_prefix):]
            add_mode_chain(logical, mode)
            add(raw)
        else:
            add(raw)
    elif '.w3mod:' not in low:
        add_mode_chain(raw, mode)
        add(raw)
    else:
        # Locale/add-on/unknown explicit namespaces should not be rewritten.
        add(raw)

    # CascLib lookup is case-insensitive on current Warcraft builds, but keep a
    # lower-case retry for older installations/tools that behaved differently.
    for candidate in list(out):
        if candidate.lower() != candidate:
            add(candidate.lower())
    return out


class CascError(RuntimeError):
    pass


class CascStorage:
    def __init__(self, install_path: str | Path | None = None, dll_path: str | Path | None = None):
        self.install_path = Path(install_path) if install_path else detect_warcraft_install()
        self.dll_path = Path(dll_path) if dll_path else default_dll_path()
        self._dll = None
        self._h = ctypes.c_void_p()

    @property
    def available(self) -> bool:
        return bool(self.install_path and self.dll_path.exists())

    def _bind(self):
        if os.name != "nt":
            raise CascError("CASC local requer Windows")
        if not self.dll_path.exists():
            try:
                self.dll_path=ensure_casclib()
            except Exception as exc:
                raise CascError(f"CascLib.dll não encontrada e download automático falhou: {exc}") from exc
        if not self.dll_path.exists():
            raise CascError(f"CascLib.dll não encontrada: {self.dll_path}")
        dll=ctypes.WinDLL(str(self.dll_path), use_last_error=True)
        dll.CascOpenStorage.argtypes=[ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        dll.CascOpenStorage.restype=ctypes.c_bool
        dll.CascOpenFile.argtypes=[ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        dll.CascOpenFile.restype=ctypes.c_bool
        if hasattr(dll, "CascGetFileSize64"):
            dll.CascGetFileSize64.argtypes=[ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint64)]
            dll.CascGetFileSize64.restype=ctypes.c_bool
        if hasattr(dll, "CascGetFileSize"):
            dll.CascGetFileSize.argtypes=[ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            dll.CascGetFileSize.restype=ctypes.c_uint32
        dll.CascReadFile.argtypes=[ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        dll.CascReadFile.restype=ctypes.c_bool
        dll.CascCloseFile.argtypes=[ctypes.c_void_p]; dll.CascCloseFile.restype=ctypes.c_bool
        dll.CascCloseStorage.argtypes=[ctypes.c_void_p]; dll.CascCloseStorage.restype=ctypes.c_bool
        self._dll=dll

    def open(self):
        if not self.install_path:
            raise CascError("Instalação do Warcraft III não encontrada. Defina WC3_INSTALL_PATH ou escolha a pasta no Studio.")
        self._bind()
        ctypes.set_last_error(0)
        h=ctypes.c_void_p()
        enc="mbcs" if os.name=="nt" else "utf-8"
        base=str(self.install_path)
        roots=[base]
        retail=self.install_path / "_retail_"
        if retail.is_dir():
            roots.append(str(retail))
        attempts=[]
        for root in roots:
            attempts.extend([root,root+":w3"])
        ok=False; last_err=0
        for param in attempts:
            ctypes.set_last_error(0); h=ctypes.c_void_p()
            raw=param.encode(enc,"replace")
            with _LOCK:
                ok=bool(self._dll.CascOpenStorage(raw, CASC_LOCALE_ALL, ctypes.byref(h)))
            if ok and h.value:
                break
            last_err=ctypes.get_last_error()
        if not ok or not h.value:
            raise CascError(f"CascOpenStorage falhou ({last_err}) em {self.install_path} (root/_retail_ e :w3 testados)")
        self._h=h
        LOGGER.info("CASC opened: %s handle=0x%x", self.install_path, int(h.value))
        return self

    def close(self):
        if self._dll and self._h and self._h.value:
            try:
                with _LOCK: self._dll.CascCloseStorage(self._h)
            finally:
                LOGGER.info("CASC closed: %s", self.install_path)
                self._h=ctypes.c_void_p()

    def __enter__(self):
        return self.open()

    def __exit__(self, *_):
        self.close()

    def _open_file(self, path: str):
        if not self._h.value:
            raise CascError("CASC storage não está aberto")
        encoded=path.encode("mbcs" if os.name=="nt" else "utf-8", "replace")
        low=path.casefold()
        if low.startswith("ptbr.w3mod:"):
            locale_masks=(CASC_LOCALE_PTBR, CASC_LOCALE_ALL, CASC_LOCALE_NONE)
        elif low.startswith("enus.w3mod:"):
            locale_masks=(CASC_LOCALE_ENUS, CASC_LOCALE_ALL, CASC_LOCALE_NONE)
        else:
            # Locale NONE is important for namespaced WC3 entries on some
            # CascLib builds; ALL remains the normal/default lookup.
            locale_masks=(CASC_LOCALE_ALL, CASC_LOCALE_NONE, CASC_LOCALE_PTBR, CASC_LOCALE_ENUS)
        for locale in locale_masks:
            fh=ctypes.c_void_p(); ctypes.set_last_error(0)
            with _LOCK:
                ok=bool(self._dll.CascOpenFile(self._h, encoded, locale, CASC_OPEN_BY_NAME, ctypes.byref(fh)))
            if ok and fh.value:
                return fh
        return None

    def has(self, ref: str) -> bool:
        # Some WC3 CASC builds return a valid handle with size 0 for names that
        # are not actually present. Use the same size-validated path as read().
        try:
            _actual,data=self.read_with_path(ref)
            return bool(data)
        except (FileNotFoundError, CascError):
            return False

    @staticmethod
    def _asset_ref_aliases(ref: str) -> list[str]:
        """Logical WC3 texture names do not always match the CASC filename.

        Reforged MDX files frequently keep ``.tif`` references while the shipped
        payload is ``.dds``.  Classic object profiles likewise keep ``.blp``
        command-button paths that can resolve to DDS in the HD overlay.  The game
        performs this compatibility lookup internally; the Studio mirrors it.
        """
        raw=(ref or "").strip().strip('"')
        if not raw:
            return []
        out=[raw]
        low=raw.casefold()
        def add(value: str):
            if value and value.casefold() not in {x.casefold() for x in out}:
                out.append(value)
        for ext in (".tif", ".tga", ".blp"):
            if low.endswith(ext):
                add(raw[:-len(ext)] + ".dds")
        if low.endswith(".dds"):
            add(raw[:-4] + ".blp")
            add(raw[:-4] + ".tif")
        return out

    def read_with_path(self, ref: str, near: str | None = None, preference: str = "hd") -> tuple[str, bytes]:
        tried=[]
        for logical_ref in self._asset_ref_aliases(ref):
            for candidate in casc_path_candidates(logical_ref, near=near, preference=preference):
                tried.append(candidate)
                fh=self._open_file(candidate)
                if not fh:
                    continue
                try:
                    size=ctypes.c_uint64()
                    if hasattr(self._dll, "CascGetFileSize64"):
                        with _LOCK: ok=bool(self._dll.CascGetFileSize64(fh, ctypes.byref(size)))
                        if not ok:
                            continue
                        total=int(size.value)
                    elif hasattr(self._dll, "CascGetFileSize"):
                        high=ctypes.c_uint32()
                        with _LOCK: low_size=int(self._dll.CascGetFileSize(fh,ctypes.byref(high)))
                        if low_size == 0xFFFFFFFF:
                            continue
                        total=(int(high.value)<<32) | low_size
                    else:
                        raise CascError("CascLib sem função de tamanho de arquivo")
                    if total <= 0 or total > 2_000_000_000:
                        continue
                    buf=(ctypes.c_ubyte * total)(); read=ctypes.c_uint32()
                    with _LOCK: ok=bool(self._dll.CascReadFile(fh, buf, total, ctypes.byref(read)))
                    if not ok:
                        err=ctypes.get_last_error(); raise CascError(f"CascReadFile falhou ({err}) para {candidate}")
                    data=bytes(buf[:int(read.value)])
                    LOGGER.info("CASC read: %s (%d bytes)", candidate, len(data))
                    return candidate,data
                finally:
                    with _LOCK: self._dll.CascCloseFile(fh)
        LOGGER.debug("CASC miss: %s; tentativas=%s", ref, tried[:10])
        raise FileNotFoundError(f"CASC não contém {ref}; tentativas: {tried[:10]}")

    def read(self, ref: str) -> bytes:
        return self.read_with_path(ref)[1]

    def read_first(self, refs: Iterable[str]) -> tuple[str, bytes]:
        last=None
        for ref in refs:
            try:
                return self.read_with_path(ref)
            except FileNotFoundError as exc:
                last=exc
        raise last or FileNotFoundError("nenhum caminho CASC fornecido")


def get_shared_casc(install_path: str | Path | None = None) -> CascStorage:
    """Return a process-wide opened Warcraft CASC storage.

    Opening Reforged CASC costs hundreds of milliseconds on Windows. The UI used
    to open/close a separate storage for model preview, unit icons and ability
    icons on every click. CascLib calls are already serialized by ``_LOCK``, so
    keeping one storage handle alive removes that startup cost without allowing
    concurrent native calls.
    """
    global _SHARED_CASC, _SHARED_CASC_KEY
    install = Path(install_path).expanduser().resolve() if install_path else detect_warcraft_install()
    if not install:
        raise CascError("Instalação do Warcraft III não encontrada. Configure pelo botão CASC…")
    key = str(install).casefold()
    with _SHARED_LOCK:
        current = _SHARED_CASC
        if current is not None and _SHARED_CASC_KEY == key and getattr(current, "_h", None) and current._h.value:
            LOGGER.debug("CASC shared reuse: %s handle=0x%x", install, int(current._h.value))
            return current
        if current is not None:
            try:
                current.close()
            except Exception:
                LOGGER.debug("Failed closing previous shared CASC", exc_info=True)
        started = __import__("time").perf_counter()
        current = CascStorage(install).open()
        _SHARED_CASC = current
        _SHARED_CASC_KEY = key
        LOGGER.info("CASC shared ready: %s in %.1f ms", install, (__import__("time").perf_counter()-started)*1000.0)
        return current


def reset_shared_casc() -> None:
    """Close and forget the process-wide CASC handle."""
    global _SHARED_CASC, _SHARED_CASC_KEY
    with _SHARED_LOCK:
        current = _SHARED_CASC
        _SHARED_CASC = None
        _SHARED_CASC_KEY = ""
        if current is not None:
            try:
                current.close()
            except Exception:
                LOGGER.debug("Failed closing shared CASC", exc_info=True)


atexit.register(reset_shared_casc)
