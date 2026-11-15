from __future__ import annotations

import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .casc import CascStorage
from .native_data import parse_slk, parse_profile_text
from .objectmod import TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING
from .fields import FieldDef

LOGGER = logging.getLogger("wc3studio")

DOODAD_DATA_FILE = "Doodads.slk"
DOODAD_METADATA_FILE = "DoodadMetaData.slk"
DOODAD_FALLBACK_BASE = "https://raw.githubusercontent.com/WarRaft/War3.mpq/main/extract/Doodads/"


def _native_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "native_data"


def ensure_doodad_fallback_data(data_dir: Path | None = None, timeout: float = 10.0) -> tuple[list[str], list[str]]:
    folder = Path(data_dir or _native_data_dir())
    folder.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    failed: list[str] = []
    for name in (DOODAD_DATA_FILE, DOODAD_METADATA_FILE):
        path = folder / name
        if path.exists() and path.stat().st_size > 100:
            continue
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            req = urllib.request.Request(
                DOODAD_FALLBACK_BASE + name,
                headers={"User-Agent": "WC3-Object-Studio/3.10.7"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
            if len(data) < 100:
                raise RuntimeError("download pequeno demais")
            tmp.write_bytes(data)
            tmp.replace(path)
            downloaded.append(name)
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    return downloaded, failed


def _read_local_slk(name: str) -> tuple[str, bytes] | None:
    path = _native_data_dir() / name
    if path.is_file() and path.stat().st_size > 100:
        try:
            return f"offline:{name}", path.read_bytes()
        except Exception:
            LOGGER.debug("Failed reading offline doodad data %s", path, exc_info=True)
    return None


def _read_casc_or_local(casc: CascStorage, refs: tuple[str, ...], local_name: str) -> tuple[str, bytes]:
    try:
        return casc.read_first(refs)
    except Exception:
        local = _read_local_slk(local_name)
        if local is not None:
            LOGGER.info("Doodad fallback: %s -> %s", local_name, local[0])
            return local
        raise


def _doodad_rawcode(row: dict[str, object]) -> str:
    folded = {str(key).casefold(): value for key, value in (row or {}).items()}
    return str(folded.get("doodid") or folded.get("doodadid") or folded.get("id") or "").strip()


def _doodad_model_base(row: dict[str, object], preference: str = "hd") -> str:
    """Resolve a doodad model base across DE, Reforged HD and Classic SD.

    Patch 3.0 adds a third Definitive Edition art set. Doodad skin/profile rows
    may expose ``file:de`` / ``model:de``; when they do not, the neutral logical
    path is still passed to the CASC resolver, which probes the selected tree.
    """
    folded = {str(key).casefold(): value for key, value in (row or {}).items()}
    pref = (preference or "hd").casefold()
    de=("file:de", "file_de", "filede", "model:de", "model_de", "modelde", "modelpath:de", "modelpath_de")
    hd=("file:hd", "file_hd", "filehd", "model:hd", "model_hd", "modelhd", "modelpath:hd", "modelpath_hd")
    neutral=("file", "filename", "model", "path", "modelpath")
    sd=("file:sd", "file_sd", "filesd", "model:sd", "model_sd", "modelsd", "modelpath:sd", "modelpath_sd")
    if pref in {"de", "definitive", "definitive edition"}:
        keys=de + neutral + hd + sd
    elif pref in {"classic", "sd"}:
        keys=sd + neutral + hd + de
    else:
        keys=hd + neutral + sd + de
    value = ""
    for key in keys:
        candidate = str(folded.get(key) or "").strip()
        if candidate and candidate not in {"-", "_"}:
            value = candidate
            break
    dir_value = str(folded.get("dir") or folded.get("directory") or "").strip()
    value = value.strip().strip('"').replace("/", "\\").lstrip("\\")
    dir_value = dir_value.strip().strip('"').replace("/", "\\").strip("\\")
    if dir_value and value:
        low_value = value.casefold()
        low_dir = dir_value.casefold()
        if "\\" not in value or not (low_value == low_dir or low_value.startswith(low_dir + "\\")):
            if "\\" not in value:
                value = dir_value + "\\" + value
    elif dir_value and not value:
        value = dir_value
    while "\\\\" in value:
        value = value.replace("\\\\", "\\")
    return value


def _merge_doodad_skin_rows(
    doodads: dict[str, dict[str, object]],
    skins: dict[str, dict[str, str]],
) -> tuple[int, int, int]:
    """Merge Reforged DoodadSkins.txt over Doodads.slk."""
    added = merged = recovered = 0
    for rawcode, skin in skins.items():
        if len(rawcode) != 4:
            continue
        row = doodads.get(rawcode)
        if row is None:
            row = {"doodID": rawcode}
            doodads[rawcode] = row
            added += 1
        folded_keys = {str(k).casefold(): k for k in row}
        changed = False
        before = _doodad_model_base(row, "hd")
        for key, value in skin.items():
            if value in (None, ""):
                continue
            existing = folded_keys.get(str(key).casefold())
            if existing is not None:
                if row.get(existing) != value:
                    row[existing] = value
                    changed = True
            else:
                row[key] = value
                folded_keys[str(key).casefold()] = key
                changed = True
        after = _doodad_model_base(row, "hd")
        if after:
            row["modelPath"] = after
        if changed:
            merged += 1
        if not before and after:
            recovered += 1
    return added, merged, recovered


def _load_doodad_skins(casc: CascStorage) -> tuple[str, dict[str, dict[str, str]]] | None:
    refs = (
        "war3.w3mod:Doodads/DoodadSkins.txt",
        "war3.w3mod:doodads/doodadskins.txt",
        "Doodads/DoodadSkins.txt",
        "doodads/doodadskins.txt",
    )
    try:
        actual, data = casc.read_first(refs)
    except Exception:
        LOGGER.warning("Reforged DoodadSkins.txt unavailable; native doodad art may be incomplete")
        return None
    try:
        parsed = parse_profile_text(data.decode("utf-8-sig", "replace"))
        LOGGER.info("Native doodad skins loaded: %s sections=%d", actual, len(parsed))
        return actual, parsed
    except Exception:
        LOGGER.warning("Failed parsing Reforged DoodadSkins.txt: %s", actual, exc_info=True)
        return None


def _merge_doodad_fallback_rows(
    doodads: dict[str, dict[str, object]],
    fallback_rows: list[dict[str, object]],
) -> tuple[int, int, int]:
    """Fill holes in CASC Doodads.slk from the bundled classic snapshot.

    Reforged currently exposes more doodads than the classic table, so the CASC
    row remains authoritative. The local snapshot is used only when a CASC field
    is absent/blank, especially the ``file`` model path that is missing for some
    stock rows on certain installs.
    """
    added = enriched = recovered_models = 0
    for fallback in fallback_rows:
        rawcode = _doodad_rawcode(fallback)
        if len(rawcode) != 4:
            continue
        current = doodads.get(rawcode)
        if current is None:
            current = dict(fallback)
            doodads[rawcode] = current
            added += 1
        else:
            folded_current = {str(k).casefold(): k for k in current}
            changed = False
            for key, value in fallback.items():
                if value in (None, ""):
                    continue
                existing_key = folded_current.get(str(key).casefold())
                existing_value = current.get(existing_key) if existing_key is not None else None
                if existing_value in (None, ""):
                    current[key] = value
                    folded_current[str(key).casefold()] = key
                    changed = True
            if changed:
                enriched += 1
        before = str(current.get("modelPath") or "").strip()
        base = _doodad_model_base(current)
        if base:
            current["modelPath"] = base
            if not before:
                recovered_models += 1
    return added, enriched, recovered_models


def load_doodad_catalog(casc: CascStorage) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    """Load native doodad rows and DoodadMetaData from Warcraft CASC.

    The metadata table is what maps binary object field ids (dfil/dmis/...) to
    Doodads.slk columns. Keeping it here makes native browsing match World
    Editor much more closely than a small hard-coded field subset.
    """
    doodads: dict[str, dict[str, object]] = {}
    metadata: dict[str, dict[str, object]] = {}

    actual, data = _read_casc_or_local(
        casc,
        (
            "war3.w3mod:Doodads/Doodads.slk",
            "war3.w3mod:doodads/Doodads.slk",
            "Doodads/Doodads.slk",
            "doodads/Doodads.slk",
        ),
        DOODAD_DATA_FILE,
    )
    for row in parse_slk(data.decode("utf-8-sig", "replace")):
        rawcode = _doodad_rawcode(row)
        if len(rawcode) != 4:
            continue
        item = dict(row)
        filename = _doodad_model_base(item)
        if filename:
            item["modelPath"] = filename
        doodads[rawcode] = item

    # Do not wait for the whole CASC table to fail before using the fallback.
    # Some Reforged installs return valid rows whose art ``file`` cell is blank.
    # The first-run setup already keeps a classic Doodads.slk snapshot locally;
    # merge only missing cells so new Reforged-only rows remain untouched.
    local = _read_local_slk(DOODAD_DATA_FILE)
    if local is not None:
        try:
            fallback_rows = parse_slk(local[1].decode("utf-8-sig", "replace"))
            added, enriched, recovered = _merge_doodad_fallback_rows(doodads, fallback_rows)
            LOGGER.info(
                "Native doodad fallback merge: added=%d enriched=%d model_paths_recovered=%d source=%s",
                added, enriched, recovered, local[0],
            )
        except Exception:
            LOGGER.warning("Failed merging local Doodads.slk fallback", exc_info=True)

    skin_bundle = _load_doodad_skins(casc)
    if skin_bundle is not None:
        skin_actual, skins = skin_bundle
        added, merged, recovered = _merge_doodad_skin_rows(doodads, skins)
        LOGGER.info(
            "Native doodad skin merge: added=%d merged=%d model_paths_recovered=%d source=%s",
            added, merged, recovered, skin_actual,
        )
    LOGGER.info("Native doodad catalog: %d rawcodes (%s)", len(doodads), actual)

    meta_actual, meta_data = _read_casc_or_local(
        casc,
        (
            "war3.w3mod:Doodads/DoodadMetaData.slk",
            "war3.w3mod:doodads/DoodadMetaData.slk",
            "Doodads/DoodadMetaData.slk",
            "doodads/DoodadMetaData.slk",
        ),
        DOODAD_METADATA_FILE,
    )
    for row in parse_slk(meta_data.decode("utf-8-sig", "replace")):
        folded = {str(key).casefold(): value for key, value in row.items()}
        field_id = str(folded.get("id") or "").strip()
        if len(field_id) != 4:
            continue
        metadata[field_id] = dict(row)
    LOGGER.info("Native doodad metadata: %d fields (%s)", len(metadata), meta_actual)
    return doodads, metadata


def _ci(row: dict[str, object], key: str, default=""):
    folded = {str(k).casefold(): v for k, v in row.items()}
    return folded.get(key.casefold(), default)




def doodad_model_candidates(doodad_row: dict[str, object], rawcode: str = "", preference: str = "hd") -> list[str]:
    """Return logical model candidates for a Doodads.slk row.

    Blizzard doodads frequently store ``file`` as a *base* path and keep the
    actual model as ``Base0.mdx``, ``Base1.mdx`` ... according to ``numVar``.
    Treating ``file`` as a literal model path is why many native doodads showed
    only their name and never reached the viewer.
    """
    folded={str(k).casefold(): v for k,v in (doodad_row or {}).items()}
    base=_doodad_model_base(doodad_row, preference)
    if not base:
        return []
    out=[]
    def add(value: str):
        value=(value or "").strip()
        if value and value.casefold() not in {x.casefold() for x in out}:
            out.append(value)
    add(base)
    stem=base
    ext=""
    low=base.casefold()
    if low.endswith((".mdx", ".mdl")):
        stem,ext=base.rsplit(".",1); ext="."+ext
    try:
        num_var=max(0,int(float(str(folded.get("numvar") or 0).strip())))
    except Exception:
        num_var=0
    # WC3 uses zero-based suffixes for most doodad variation model sets. Also
    # try one-based suffixes because a handful of classic assets use that form.
    if num_var>0:
        for i in range(num_var):
            add(f"{stem}{i}{ext}")
        for i in range(1,num_var+1):
            add(f"{stem}{i}{ext}")
    # Some table rows already encode a variation in the rawcode but omit numVar.
    # The 0 suffix is a cheap and safe fallback for the common Base0.mdx layout.
    if num_var==0 and rawcode:
        add(f"{stem}0{ext}")
    return out


def doodad_display_name(doodad_row: dict[str, object], rawcode: str = "", resolver=None) -> str:
    """Human-readable doodad name, resolving World Editor localization tokens."""
    folded={str(k).casefold(): v for k,v in (doodad_row or {}).items()}
    name=str(folded.get("name") or "").strip()
    if name:
        resolved = resolver(name) if resolver is not None else name
        resolved = str(resolved or "").strip()
        if resolved and not resolved.upper().startswith("WESTRING_"):
            return resolved
    comment=str(folded.get("comment") or folded.get("editorname") or "").strip()
    if comment:
        resolved = resolver(comment) if resolver is not None else comment
        if str(resolved or "").strip():
            return str(resolved).strip()
    return rawcode or name

def _boolish(value: object) -> int:
    low = str(value).strip().casefold()
    if low in {"true", "yes", "y"}:
        return 1
    if low in {"false", "no", "n", ""}:
        return 0
    try:
        return int(float(str(value).strip()))
    except Exception:
        return 0


def doodad_field_value(
    doodad_row: dict[str, object],
    metadata: dict[str, dict[str, object]],
    field_id: str,
    *,
    variation: int = 0,
) -> tuple[object | None, str, int]:
    """Resolve a native doodad object field through DoodadMetaData.slk."""
    meta = metadata.get(field_id, {})
    column = str(_ci(meta, "field", "") or "").strip()
    if not column:
        # compatibility aliases for older builds/tests
        fallback = {
            "dnam": "Name", "dfil": "file", "dmis": "minScale", "dmas": "maxScale",
            "dvar": "numVar", "dsel": "selSize",
        }
        column = fallback.get(field_id, field_id)
    repeat = _boolish(_ci(meta, "repeat", 0))
    if repeat > 0:
        # Doodads.slk stores repeated variation columns as vertR01..vertR10.
        idx = max(1, min(repeat, int(variation or 1)))
        candidate = f"{column}{idx:02d}"
    else:
        candidate = column
    folded = {str(k).casefold(): v for k, v in doodad_row.items()}
    value = folded.get(candidate.casefold())
    used = candidate
    if value in (None, "") and column.casefold() in folded:
        value = folded[column.casefold()]
        used = column
        # Some Reforged doodad rows store repeated variation values as a
        # comma-separated list in the unsuffixed base column instead of
        # materializing vertR01..vertR10-style columns.  When metadata says
        # repeat>0, pick the requested variation here before numeric coercion;
        # otherwise int/float conversion receives the whole list and crashes.
        if repeat > 0:
            if isinstance(value, (list, tuple)):
                parts = [str(x).strip() for x in value]
            else:
                parts = [part.strip() for part in str(value).split(",")]
            parts = [part for part in parts if part != ""]
            if parts:
                pick = max(1, min(int(variation or 1), len(parts))) - 1
                value = parts[pick]
                used = f"{column}[{pick + 1}]"
    if field_id == "dfil" and value in (None, ""):
        recovered = _doodad_model_base(doodad_row)
        if recovered:
            value = recovered
            used = "modelPath(fallback)"
    if field_id == "dnam" and str(value or "").strip().upper().startswith("WESTRING_"):
        friendly=doodad_display_name(doodad_row)
        if friendly and not friendly.upper().startswith("WESTRING_"):
            value=friendly
            used="comment"
    editor_type = str(_ci(meta, "type", "string") or "string").casefold()
    raw_text = str(value).strip() if value is not None else ""
    # Blizzard uses '-' and '_' as no-value sentinels in native SLKs.
    # Never feed those into float()/int(); that used to crash the Tk callback.
    if value in (None, "") or raw_text in {"-", "_"}:
        value_type = TYPE_INT if editor_type in {"int", "bool"} else (TYPE_UNREAL if editor_type == "unreal" else (TYPE_REAL if editor_type == "real" else TYPE_STRING))
        return None, used, value_type

    if editor_type in {"int", "bool"}:
        value_type = TYPE_INT
        value = _boolish(value) if editor_type == "bool" else int(float(raw_text))
    elif editor_type in {"real", "unreal"}:
        value_type = TYPE_UNREAL if editor_type == "unreal" else TYPE_REAL
        value = float(raw_text)
    else:
        value_type = TYPE_STRING
        value = str(value)
    return value, used, value_type


_LABELS_PT = {
    "dnam": "Nome", "dcat": "Categoria", "dtil": "Tilesets", "dtsp": "Específico de tileset",
    "dfil": "Modelo", "dsnd": "Som em loop", "dsel": "Tamanho de seleção", "ddes": "Escala padrão",
    "dmis": "Escala mínima", "dmas": "Escala máxima", "dcpr": "Escala aleatória", "duch": "Usar click helper",
    "dimc": "Ignorar clique no modelo", "dmap": "Pitch máximo", "dmar": "Roll máximo", "dvis": "Raio de visão",
    "dwlk": "Caminhável", "dvar": "Variações", "donc": "Permitir em penhascos", "donw": "Permitir na água",
    "dflt": "Flutua", "dshd": "Sombra", "dshf": "Mostrar na névoa", "danf": "Animar na névoa",
    "dfxr": "Rotação fixa", "dptx": "Textura de pathing", "dsmm": "Mostrar no minimapa", "dumc": "Usar cor no minimapa",
    "dmmr": "Minimapa R", "dmmg": "Minimapa G", "dmmb": "Minimapa B", "dvr1": "Tint R",
    "dvg1": "Tint G", "dvb1": "Tint B", "dusr": "Lista do usuário",
}

_LABELS_EN = {
    "dnam": "Name", "dcat": "Category", "dtil": "Tilesets", "dtsp": "Tileset Specific",
    "dfil": "Model", "dsnd": "Looping Sound", "dsel": "Selection Size", "ddes": "Default Scale",
    "dmis": "Minimum Scale", "dmas": "Maximum Scale", "dcpr": "Random Scale", "duch": "Use Click Helper",
    "dimc": "Ignore Model Click", "dmap": "Maximum Pitch", "dmar": "Maximum Roll", "dvis": "Visibility Radius",
    "dwlk": "Walkable", "dvar": "Variations", "donc": "Place on Cliffs", "donw": "Place on Water",
    "dflt": "Floats", "dshd": "Shadow", "dshf": "Show in Fog", "danf": "Animate in Fog",
    "dfxr": "Fixed Rotation", "dptx": "Pathing Texture", "dsmm": "Show on Minimap", "dumc": "Use Minimap Color",
    "dmmr": "Minimap R", "dmmg": "Minimap G", "dmmb": "Minimap B", "dvr1": "Tint R",
    "dvg1": "Tint G", "dvb1": "Tint B", "dusr": "User List",
}

_CATEGORY_PT = {"text": "Texto", "editor": "Editor", "art": "Arte", "sound": "Som", "path": "Pathing"}
_CATEGORY_EN = {"text": "Text", "editor": "Editor", "art": "Art", "sound": "Sound", "path": "Pathing"}


def build_doodad_field_defs(metadata: dict[str, dict[str, object]], language: str = "pt") -> dict[str, FieldDef]:
    labels = _LABELS_PT if language == "pt" else _LABELS_EN
    cats = _CATEGORY_PT if language == "pt" else _CATEGORY_EN
    rows: list[tuple[str, str, FieldDef]] = []
    for field_id, meta in metadata.items():
        column = str(_ci(meta, "field", field_id) or field_id)
        category_key = str(_ci(meta, "category", "editor") or "editor").casefold()
        category = cats.get(category_key, category_key.title())
        editor_type = str(_ci(meta, "type", "string") or "string")
        if editor_type.casefold() in {"int", "bool"}:
            value_type = TYPE_INT
        elif editor_type.casefold() == "real":
            value_type = TYPE_REAL
        elif editor_type.casefold() == "unreal":
            value_type = TYPE_UNREAL
        else:
            value_type = TYPE_STRING
        label = labels.get(field_id)
        if not label:
            label = re.sub(r"(?<!^)(?=[A-Z])", " ", column).replace("_", " ").strip().title() or field_id
        sort = str(_ci(meta, "sort", "") or "")
        fd = FieldDef(
            field_id,
            label,
            value_type,
            category,
            help=f"DoodadData.{column}",
            editor_type=editor_type,
            native_table="DoodadData",
            native_column=column,
            sort=sort,
        )
        rows.append((sort, field_id, fd))
    rows.sort(key=lambda item: (item[0], item[1]))
    return {field_id: fd for _sort, field_id, fd in rows}
