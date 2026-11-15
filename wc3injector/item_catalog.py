from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import copy
import logging
import re
from typing import Iterable

from .casc import detect_warcraft_install, get_shared_casc
from .native_data import parse_profile_text, parse_slk
from .objectmod import ObjectModFile
from .storm import StormArchive

LOGGER = logging.getLogger("wc3studio")


@dataclass
class ItemInfo:
    rawcode: str
    name: str = ""
    tooltip: str = ""
    ubertip: str = ""
    description: str = ""
    icon: str = ""
    level: int = 0
    item_class: str = ""
    equipment_type: str = ""
    source: str = "Warcraft"
    base_id: str = ""
    custom: bool = False

    @property
    def detail_text(self) -> str:
        parts=[]
        for value in (self.description, self.ubertip, self.tooltip):
            value=str(value or "").strip()
            if value and value not in parts:
                parts.append(value)
        return "\n\n".join(parts)


# Item fields are described in UnitMetaData.slk using useItem=1.
# These are the logical values needed by the chooser, not an exhaustive Item Object Editor.
COLUMN_KEYS = {
    "name": "name",
    "tip": "tooltip",
    "ubertip": "ubertip",
    "description": "description",
    "art": "icon",
    "icon": "icon",
    "level": "level",
    "class": "item_class",
    "itemclass": "item_class",
    "equipmenttype": "equipment_type",
    "equipment type": "equipment_type",
    "equiptype": "equipment_type",
}

# Compatibility fallback for common item object-data field ids.  UnitMetaData is
# the authority whenever it is present.
FIELD_FALLBACK = {
    "unam": "name",
    "utip": "tooltip",
    "utub": "ubertip",
    "ides": "description",
    "iico": "icon",
    "ilev": "level",
    "icla": "item_class",
}


def _ci_get(row: dict, *keys: str):
    folded={str(k).casefold(): v for k,v in (row or {}).items()}
    for key in keys:
        if str(key).casefold() in folded:
            value=folded[str(key).casefold()]
            if value not in (None, "", "_", "-"):
                return value
    return None


def _first(value) -> str:
    text=str(value or "").strip().strip('"')
    if not text or text in {"_", "-"}:
        return ""
    return text.split(",",1)[0].strip()


def _as_int(value, default=0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def _resolve_text(session, value) -> str:
    text=str(value or "").strip().strip('"')
    if not text or text in {"_", "-"}:
        return ""
    try:
        text=session.resolve_string(text) if session is not None else text
    except Exception:
        pass
    # Make Warcraft formatting readable in a normal Tk text widget. Preserve the
    # wording but remove in-game color/formatting control codes.
    text=str(text).replace("|n","\n").replace("\\n","\n")
    text=re.sub(r"\|c[0-9A-Fa-f]{8}","",text)
    text=text.replace("|r","").replace("|R","")
    return text.strip()


def _profile_merge(casc, filenames: Iterable[str]) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]]={}
    for filename in filenames:
        refs=(
            f"units/{filename}", f"war3.w3mod:units/{filename}",
            f"war3sd.w3mod:units/{filename}", f"war3.w3mod:_classic.w3mod:units/{filename}",
        )
        try:
            actual,data=casc.read_first(refs)
        except Exception:
            LOGGER.debug("CASC item profile missing: %s", filename)
            continue
        try:
            parsed=parse_profile_text(data.decode("utf-8-sig","replace"))
            for raw,row in parsed.items():
                merged.setdefault(raw,{}).update(row)
            LOGGER.info("CASC item profile loaded: %s sections=%d",actual,len(parsed))
        except Exception:
            LOGGER.warning("Failed parsing item profile %s", filename, exc_info=True)
    return merged


def _load_item_data_from_casc(casc) -> dict[str, dict]:
    refs=(
        "units/ItemData.slk", "war3.w3mod:units/ItemData.slk",
        "war3sd.w3mod:units/ItemData.slk", "war3.w3mod:_classic.w3mod:units/ItemData.slk",
    )
    actual,data=casc.read_first(refs)
    rows=parse_slk(data.decode("utf-8-sig","replace"))
    out={}
    for row in rows:
        raw=str(_ci_get(row,"itemID","itemId","ID") or "").strip()
        if len(raw)==4:
            out[raw]=dict(row)
    LOGGER.info("CASC ItemData loaded: %s rows=%d",actual,len(out))
    return out


def _metadata_map(casc=None) -> dict[str, str]:
    """field id -> logical chooser property using UnitMetaData useItem rows."""
    out=dict(FIELD_FALLBACK)
    def merge_rows(rows):
        for row in rows:
            fid=str(row.get("ID") or "").strip()
            try: use_item=int(row.get("useItem",0) or 0)
            except Exception: use_item=0
            if len(fid)!=4 or not use_item:
                continue
            column=str(row.get("field") or "").strip().casefold()
            normalized=column.replace("_","").replace(" ","")
            logical=COLUMN_KEYS.get(column) or COLUMN_KEYS.get(normalized)
            if logical:
                out[fid]=logical

    path=Path(__file__).resolve().parent.parent/"native_data"/"UnitMetaData.slk"
    if path.exists():
        try: merge_rows(parse_slk(path.read_text(encoding="utf-8",errors="replace")))
        except Exception: LOGGER.warning("Could not parse bundled useItem metadata",exc_info=True)
    if casc is not None:
        try:
            _actual,data=casc.read_first(("units/UnitMetaData.slk","war3.w3mod:units/UnitMetaData.slk"))
            merge_rows(parse_slk(data.decode("utf-8-sig","replace")))
        except Exception:
            LOGGER.debug("Live CASC UnitMetaData unavailable for item fields",exc_info=True)
    return out


def _equipment_value(row: dict, profile: dict) -> str:
    # Newer Reforged builds expose equipment type under build-dependent names.
    for source in (profile,row):
        for key,value in (source or {}).items():
            folded=str(key).casefold().replace("_","").replace(" ","")
            if "equipmenttype" in folded or folded in {"equiptype","itemequiptype"}:
                text=_first(value)
                if text:
                    return text
    return ""


def _native_item(session, raw: str, row: dict, profile: dict) -> ItemInfo:
    name=_ci_get(profile,"Name","name") or _ci_get(row,"Name","name") or raw
    tip=_ci_get(profile,"Tip","tip") or _ci_get(row,"Tip","tip")
    uber=_ci_get(profile,"Ubertip","ubertip") or _ci_get(row,"Ubertip","ubertip")
    desc=_ci_get(profile,"Description","description") or _ci_get(row,"Description","description")
    icon=_ci_get(profile,"Art:hd","Art","art","Icon:hd","Icon","icon") or _ci_get(row,"Art","art","Icon","icon")
    level=_ci_get(row,"Level","level") or _ci_get(profile,"Level","level")
    item_class=_ci_get(row,"class","Class","itemClass") or _ci_get(profile,"class","Class","itemClass")
    equip=_equipment_value(row,profile)
    return ItemInfo(
        rawcode=raw,
        name=_resolve_text(session,name) or raw,
        tooltip=_resolve_text(session,tip),
        ubertip=_resolve_text(session,uber),
        description=_resolve_text(session,desc),
        icon=_first(icon), level=_as_int(level),
        item_class=_resolve_text(session,item_class),
        equipment_type=_resolve_text(session,equip),
        source="Warcraft", base_id=raw, custom=False,
    )


def _apply_mods(session, info: ItemInfo, record, field_map: dict[str,str]) -> ItemInfo:
    result=copy.deepcopy(info)
    for mod in record.modifications:
        logical=field_map.get(mod.field_id)
        if not logical:
            continue
        value=mod.value
        if logical=="level":
            result.level=_as_int(value,result.level)
        elif logical in {"name","tooltip","ubertip","description","item_class","equipment_type"}:
            setattr(result,logical,_resolve_text(session,value))
        elif logical=="icon":
            result.icon=_first(value)
    return result


def load_item_catalog(session) -> list[ItemInfo]:
    """Return native Warcraft items plus map-local w3t originals/custom items.

    The catalog is read-only.  war3map.w3t stays protected by the Studio; this
    feature only uses it to make inventory/drop selection visual and friendly.
    """
    native: dict[str,ItemInfo]={}
    casc=None
    try:
        install=detect_warcraft_install()
        if install:
            casc=get_shared_casc(install)
            item_rows=_load_item_data_from_casc(casc)
            profiles=_profile_merge(casc,("ItemFunc.txt","ItemStrings.txt","ItemSkin.txt"))
            for raw,row in item_rows.items():
                native[raw]=_native_item(session,raw,row,profiles.get(raw,{}))
    except Exception:
        LOGGER.warning("Native Warcraft item catalog unavailable",exc_info=True)

    # Keep an immutable native-default view: a custom item inherits Blizzard's
    # base item, not a map author's override to that stock rawcode.
    defaults={raw:copy.deepcopy(info) for raw,info in native.items()}
    merged={raw:copy.deepcopy(info) for raw,info in native.items()}
    field_map=_metadata_map(casc)

    try:
        with StormArchive(session.map_path,read_only=True) as arc:
            if arc.has("war3map.w3t"):
                obj=ObjectModFile.from_bytes(arc.read("war3map.w3t"),"item")
                for rec in obj.original:
                    raw=rec.custom_id if rec.custom_id.strip("\0") else rec.original_id
                    base=merged.get(raw) or defaults.get(rec.original_id) or ItemInfo(rawcode=raw,name=raw,base_id=rec.original_id)
                    edited=_apply_mods(session,base,rec,field_map)
                    edited.rawcode=raw; edited.source="Mapa · item nativo alterado"; edited.base_id=rec.original_id; edited.custom=False
                    merged[raw]=edited
                for rec in obj.custom:
                    raw=rec.custom_id
                    base=copy.deepcopy(defaults.get(rec.original_id) or merged.get(rec.original_id) or ItemInfo(rawcode=raw,name=raw,base_id=rec.original_id))
                    base.rawcode=raw; base.base_id=rec.original_id; base.custom=True; base.source="Mapa · item custom"
                    edited=_apply_mods(session,base,rec,field_map)
                    edited.rawcode=raw; edited.base_id=rec.original_id; edited.custom=True; edited.source="Mapa · item custom"
                    if not edited.name or edited.name==rec.original_id:
                        edited.name=raw
                    merged[raw]=edited
    except Exception:
        LOGGER.warning("Could not read map custom item catalog",exc_info=True)

    rows=list(merged.values())
    rows.sort(key=lambda x:(0 if x.custom else 1,(x.name or x.rawcode).casefold(),x.rawcode.casefold()))
    LOGGER.info("Item chooser catalog ready: total=%d custom=%d native=%d",len(rows),sum(1 for x in rows if x.custom),sum(1 for x in rows if not x.custom))
    return rows
