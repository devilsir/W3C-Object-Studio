from __future__ import annotations

import logging
from typing import Iterable

from .casc import CascStorage
from .native_data import parse_profile_text, parse_slk

LOGGER = logging.getLogger("wc3studio")

UNIT_PROFILE_FILES = (
    "HumanUnitFunc.txt",
    "OrcUnitFunc.txt",
    "UndeadUnitFunc.txt",
    "NightElfUnitFunc.txt",
    "NeutralUnitFunc.txt",
    "CampaignUnitFunc.txt",
)

# Reforged keeps visual overrides in UnitSkin.txt.  The file can contain
# Art/Art:sd and file/file:sd-style keys depending on build/graphics mode.
UNIT_SKIN_FILES = (
    "UnitSkin.txt",
    "UnitWeaponsSkin.txt",
)

_PROFILE_CACHE: dict[str, dict[str, dict[str, str]]] = {}
_UNIT_UI_CACHE: dict[str, dict[str, dict[str, object]]] = {}


def _ci_get(row: dict[str, str], keys: Iterable[str]) -> str:
    folded = {str(k).casefold(): str(v).strip() for k, v in row.items()}
    for key in keys:
        value = folded.get(str(key).casefold())
        if value:
            return value
    return ""


def _first_value(value: str) -> str:
    """Profile fields may be comma-separated arrays; visual fields use slot 0."""
    value = str(value or "").strip()
    if not value:
        return ""
    return value.split(",", 1)[0].strip()


def load_unit_profiles(casc: CascStorage) -> dict[str, dict[str, str]]:
    cache_key = str(casc.install_path).casefold()
    if cache_key in _PROFILE_CACHE:
        return _PROFILE_CACHE[cache_key]

    merged: dict[str, dict[str, str]] = {}

    # Base functional profiles first.
    for filename in UNIT_PROFILE_FILES:
        refs = [f"units/{filename}", f"war3.w3mod:units/{filename}"]
        try:
            actual, data = casc.read_first(refs)
        except Exception:
            LOGGER.debug("CASC unit profile missing: %s", filename)
            continue
        try:
            parsed = parse_profile_text(data.decode("utf-8-sig", "replace"))
            for raw, row in parsed.items():
                merged.setdefault(raw, {}).update(row)
            LOGGER.info("CASC unit profile loaded: %s sections=%d", actual, len(parsed))
        except Exception:
            LOGGER.warning("Failed parsing CASC unit profile %s", filename, exc_info=True)

    # Skin overlays last so Reforged art/model keys win over base/classic ones.
    for filename in UNIT_SKIN_FILES:
        refs = [f"units/{filename}", f"war3.w3mod:units/{filename}"]
        try:
            actual, data = casc.read_first(refs)
        except Exception:
            LOGGER.debug("CASC unit skin missing: %s", filename)
            continue
        try:
            parsed = parse_profile_text(data.decode("utf-8-sig", "replace"))
            for raw, row in parsed.items():
                merged.setdefault(raw, {}).update(row)
            LOGGER.info("CASC unit skin loaded: %s sections=%d", actual, len(parsed))
        except Exception:
            LOGGER.warning("Failed parsing CASC unit skin %s", filename, exc_info=True)

    _PROFILE_CACHE[cache_key] = merged
    LOGGER.info("CASC unit visual profiles ready: %d rawcodes", len(merged))
    return merged



def load_unit_ui(casc: CascStorage) -> dict[str, dict[str, object]]:
    """Load UnitUI from Reforged and fill legacy Object Editor art columns.

    The current Reforged UnitUI is intentionally sparse: many classic fields
    such as ``file``, ``scale``, ``modelScale`` and tint colors moved to skin
    data or disappeared from the modern SLK.  Prefer the live Reforged row, but
    fill cells that are genuinely absent from a local SD/classic UnitUI when
    Warcraft exposes one in CASC.  The offline native-data catalog provides a
    second compatibility fallback during startup.
    """
    cache_key = str(casc.install_path).casefold()
    if cache_key in _UNIT_UI_CACHE:
        return _UNIT_UI_CACHE[cache_key]

    def read_table(refs):
        actual, data = casc.read_first(refs)
        rows = parse_slk(data.decode("utf-8-sig", "replace"))
        table: dict[str, dict[str, object]] = {}
        for row in rows:
            raw = str(row.get("unitUIID") or row.get("unitID") or "").strip()
            if raw:
                table[raw] = dict(row)
        return actual, table

    reforged_refs = [
        "units/UnitUI.slk",
        "war3.w3mod:units/UnitUI.slk",
        "units/unitui.slk",
        "war3.w3mod:units/unitui.slk",
    ]
    classic_refs = [
        "war3sd.w3mod:units/UnitUI.slk",
        "war3sd.w3mod:units/unitui.slk",
        "war3.w3mod:_classic.w3mod:units/UnitUI.slk",
        "war3.w3mod:_classic.w3mod:units/unitui.slk",
        "war3.w3mod:_sd.w3mod:units/UnitUI.slk",
        "war3.w3mod:_sd.w3mod:units/unitui.slk",
    ]

    try:
        actual, table = read_table(reforged_refs)
        LOGGER.info("CASC UnitUI loaded: %s rows=%d", actual, len(table))
    except Exception:
        LOGGER.warning("Could not load native UnitUI.slk from CASC", exc_info=True)
        table = {}

    # Classic/SD CASC is optional.  It must never overwrite current Reforged
    # values; it only restores columns that the modern table dropped.
    try:
        classic_actual, classic = read_table(classic_refs)
        rows_added = cells_filled = 0
        for raw, row in classic.items():
            target = table.get(raw)
            if target is None:
                table[raw] = dict(row)
                rows_added += 1
                continue
            existing = {str(k).casefold(): k for k in target}
            for key, value in row.items():
                if value in (None, ""):
                    continue
                actual_key = existing.get(str(key).casefold())
                if actual_key is None or target.get(actual_key) in (None, ""):
                    target[key] = value
                    existing[str(key).casefold()] = key
                    cells_filled += 1
        LOGGER.info(
            "CASC classic UnitUI compatibility merge: rows_added=%d cells_filled=%d source=%s",
            rows_added, cells_filled, classic_actual,
        )
    except Exception:
        LOGGER.debug("Classic/SD UnitUI.slk not exposed by this CASC install")

    _UNIT_UI_CACHE[cache_key] = table
    return table



def _model_key_order(preference: str = "hd") -> tuple[str, ...]:
    """Visual model keys in graphics-mode precedence order.

    Patch 3.0 may expose ``file:de``/``Model:de`` beside the established HD and
    SD keys. Neutral ``file`` remains a useful fallback because many Blizzard
    rows rely on the same logical path and let CASC select the art tree.
    """
    pref=(preference or "hd").casefold()
    de=(
        "file:de", "File:de", "file_de", "File_DE", "fileDE", "FileDE",
        "Model:de", "model:de", "Model_DE", "model_de", "ModelDE", "modelDE",
    )
    hd=(
        "file:hd", "File:hd", "file_hd", "File_HD", "fileHD", "FileHD",
        "Model:hd", "model:hd", "Model_HD", "model_hd", "ModelHD", "modelHD",
    )
    neutral=("file", "File", "Model", "model")
    sd=(
        "file:sd", "File:sd", "file_sd", "File_SD", "fileSD", "FileSD",
        "Model:sd", "model:sd", "Model_SD", "model_sd", "ModelSD", "modelSD",
    )
    if pref in {"de", "definitive", "definitive edition"}:
        return de + neutral + hd + sd
    if pref in {"classic", "sd"}:
        return sd + neutral + hd + de
    return hd + neutral + sd + de


def _icon_key_order(preference: str = "hd") -> tuple[str, ...]:
    pref=(preference or "hd").casefold()
    de=("Art:de", "Art_DE", "ArtDE", "Icon:de", "Icon_DE", "IconDE")
    hd=("Art:hd", "Art_HD", "ArtHD", "Icon:hd", "Icon_HD", "IconHD")
    neutral=("Art", "Icon", "icon")
    sd=("Art:sd", "Art_SD", "ArtSD", "Icon:sd", "Icon_SD", "IconSD")
    if pref in {"de", "definitive", "definitive edition"}:
        return de + neutral + hd + sd
    if pref in {"classic", "sd"}:
        return sd + neutral + hd + de
    return hd + neutral + sd + de


def _score_icon_key_order(preference: str = "hd") -> tuple[str, ...]:
    pref=(preference or "hd").casefold()
    de=("ScoreScreenIcon:de", "ScoreScreenIcon_DE", "ScoreScreenIconDE")
    hd=("ScoreScreenIcon:hd", "ScoreScreenIcon_HD", "ScoreScreenIconHD")
    neutral=("ScoreScreenIcon",)
    sd=("ScoreScreenIcon:sd", "ScoreScreenIcon_SD", "ScoreScreenIconSD")
    if pref in {"de", "definitive", "definitive edition"}:
        return de + neutral + hd + sd
    if pref in {"classic", "sd"}:
        return sd + neutral + hd + de
    return hd + neutral + sd + de


def _cinematic_model_aliases(ref: str) -> list[str]:
    """Return conservative aliases for old cinematic model paths.

    Reforged removed/repointed a handful of Warcraft III cinematic-only models
    while the native object data can still reference their classic ``*CIN``
    paths.  The common gameplay model is often the same path with the CIN suffix
    removed (e.g. AbominationCIN -> Abomination).
    """
    raw=str(ref or "").strip().replace('/', '\\')
    if not raw:
        return []
    out=[]
    def add(v: str):
        v=str(v or '').strip().replace('/', '\\')
        if v and v.casefold() not in {x.casefold() for x in out}:
            out.append(v)
    add(raw)
    parts=raw.split('\\')
    transformed=[]
    changed=False
    for part in parts:
        new=part
        for suffix in ('_CIN','-CIN','CIN','_cin','-cin','cin'):
            if new.endswith(suffix) and len(new)>len(suffix):
                new=new[:-len(suffix)]
                changed=True
                break
        transformed.append(new)
    if changed:
        add('\\'.join(transformed))
    return out


def unit_model_candidates(casc: CascStorage, rawcode: str, preference: str = "hd") -> list[str]:
    """Return preview model candidates for a native unit, best-first.

    Besides the explicit HD/SD profile keys and UnitUI.slk model, this includes
    safe aliases for stale cinematic ``*CIN`` paths and visually equivalent
    sibling units that share the same command-button art.  It is preview-only.
    """
    profiles=load_unit_profiles(casc)
    ui=load_unit_ui(casc)
    row=profiles.get(rawcode,{})
    ui_row=ui.get(rawcode,{})
    out=[]
    def add(v: str):
        for candidate in _cinematic_model_aliases(_first_value(v)):
            if candidate and candidate.casefold() not in {x.casefold() for x in out}:
                out.append(candidate)

    folded={str(k).casefold():str(v).strip() for k,v in row.items()}
    for key in _model_key_order(preference):
        value=folded.get(key.casefold())
        if value:
            add(value)
    ui_folded={str(k).casefold():str(v).strip() for k,v in ui_row.items()}
    for key in ('file','model'):
        value=ui_folded.get(key.casefold())
        if value:
            add(value)

    # Cinematic variants often share the exact same BTN art with their normal
    # gameplay unit. Use that as a final fallback without guessing by rawcode.
    target_icon=_first_value(_ci_get(row,(
        'Art:de','Art_DE','ArtDE','Icon:de','Icon_DE','IconDE','Art:hd','Art_HD','ArtHD','Art','Icon:hd','Icon_HD','IconHD','Icon','icon','Art:sd','Art_SD','ArtSD'
    )))
    if target_icon:
        icon_key=target_icon.casefold()
        siblings=[]
        for sibling_raw,sibling_row in profiles.items():
            if sibling_raw==rawcode:
                continue
            sibling_icon=_first_value(_ci_get(sibling_row,(
                'Art:de','Art_DE','ArtDE','Icon:de','Icon_DE','IconDE','Art:hd','Art_HD','ArtHD','Art','Icon:hd','Icon_HD','IconHD','Icon','icon','Art:sd','Art_SD','ArtSD'
            )))
            if sibling_icon.casefold()!=icon_key:
                continue
            # Prefer ordinary/raw gameplay rows over cinematic-looking aliases.
            score=0 if 'cin' not in sibling_raw.casefold() else 10
            siblings.append((score,sibling_raw,sibling_row))
        for _score,sibling_raw,sibling_row in sorted(siblings,key=lambda x:(x[0],x[1]))[:8]:
            sfold={str(k).casefold():str(v).strip() for k,v in sibling_row.items()}
            for key in _model_key_order(preference):
                value=sfold.get(key.casefold())
                if value:
                    add(value)
            su=ui.get(sibling_raw,{})
            sufold={str(k).casefold():str(v).strip() for k,v in su.items()}
            if sufold.get('file'):
                add(sufold['file'])
    return out[:24]

def resolve_unit_visual(casc: CascStorage, rawcode: str, preference: str = "hd") -> dict[str, str]:
    """Resolve model/icon visual paths for a native Warcraft unit from CASC.

    The returned values are only used for preview.  Nothing is written into the
    map and no native game data is modified.
    """
    row = load_unit_profiles(casc).get(rawcode, {})
    ui_row = load_unit_ui(casc).get(rawcode, {})

    # Reforged skin keys vary slightly across builds. Prefer HD/general keys,
    # then classic/SD fallbacks.  If the profile/skin has no model entry, use
    # UnitUI.slk:file — this is where Blizzard keeps the stock unit model path
    # for many ordinary units/heroes (e.g. Nalc).
    model_keys=_model_key_order(preference)
    model = _first_value(_ci_get(row, model_keys))
    model_source = 'UnitSkin/UnitFunc'
    if not model:
        model = _first_value(_ci_get({str(k): str(v) for k, v in ui_row.items()}, (
            'file', 'File', 'model', 'Model'
        )))
        if model:
            model_source = 'UnitUI.slk:file'

    icon = _first_value(_ci_get(row, _icon_key_order(preference)))
    # Some builds expose an icon-ish field in UnitUI; keep it as a final
    # fallback only because command-button Art from UnitFunc/UnitSkin is more
    # authoritative.
    if not icon:
        icon = _first_value(_ci_get({str(k): str(v) for k, v in ui_row.items()}, (
            'Art', 'icon', 'Icon', 'nbmmIcon'
        )))
        if icon in {'-', '_'}:
            icon = ''
    score_icon = _first_value(_ci_get(row, _score_icon_key_order(preference)))

    return {
        "model": model,
        "icon": icon,
        "score_icon": score_icon,
        "source": f"CASC unit profile {rawcode} · model={model_source}",
    }
