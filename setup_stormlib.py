from __future__ import annotations

from pathlib import Path
import hashlib
import io
import platform
import struct
import urllib.request
import zipfile
import shutil

from wc3injector.debuglog import configure_logging, install_exception_hooks, log_exception

LOGGER, _SESSION_LOG = configure_logging()
install_exception_hooks(LOGGER)

URL = "https://github.com/ladislav-zezula/StormLib/releases/download/v9.40/stormlib_dll.zip"
SHA256 = "b2c9635e7b63edee1bd7c82e7dc180d739f3accb2b8994804c7774e464ce89ae"


def _pick_dll(names: list[str]) -> str:
    dlls = [n for n in names if n.lower().endswith("stormlib.dll")]
    if not dlls:
        raise SystemExit("StormLib.dll não encontrado no ZIP oficial")

    py64 = struct.calcsize("P") == 8
    if py64:
        preferred = [n for n in dlls if "x64" in n.lower() or "amd64" in n.lower()]
        arch_name = "x64"
    else:
        preferred = [n for n in dlls if "win32" in n.lower() or "x86" in n.lower()]
        arch_name = "x86"

    if preferred:
        chosen = sorted(preferred, key=len)[0]
    else:
        # Do not silently install an obviously opposite architecture.
        opposite = [
            n for n in dlls
            if (py64 and ("win32" in n.lower() or "x86" in n.lower()))
            or ((not py64) and ("x64" in n.lower() or "amd64" in n.lower()))
        ]
        remaining = [n for n in dlls if n not in opposite]
        if not remaining:
            raise SystemExit(
                f"o pacote não contém uma StormLib compatível com o Python {arch_name}. "
                "Instale um Python com a mesma arquitetura da DLL."
            )
        chosen = sorted(remaining, key=len)[0]

    print(f"Python: {platform.python_version()} / {arch_name}")
    print(f"DLL escolhida no pacote: {chosen}")
    return chosen


def main():
    root = Path(__file__).resolve().parent
    outdir = root / "bin"
    outdir.mkdir(exist_ok=True)
    print("Baixando StormLib 9.40 oficial...")
    with urllib.request.urlopen(URL, timeout=60) as r:
        data = r.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest.lower() != SHA256.lower():
        raise SystemExit(f"SHA256 inesperado: {digest}")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        chosen = _pick_dll(zf.namelist())
        with zf.open(chosen) as src, open(outdir / "StormLib.dll", "wb") as dst:
            shutil.copyfileobj(src, dst)
    print(f"OK: {outdir / 'StormLib.dll'}")


if __name__ == "__main__":
    LOGGER.info("setup_stormlib START")
    try:
        main()
        LOGGER.info("setup_stormlib DONE")
    except BaseException as exc:
        log_exception(LOGGER, "setup_stormlib", exc)
        raise
