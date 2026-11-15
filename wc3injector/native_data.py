from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import os
import re
import urllib.request

from .fields import FieldDef
from .objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING

LOGGER = logging.getLogger("wc3studio")

# Baseline data used to resolve values inherited from Blizzard rawcodes.
#
# The numeric/object SLKs below come from a Reforged-era public mirror.  They
# are downloaded on the user's machine by setup_native_data.py and are NOT
# bundled in the application ZIP.  Profile TXT files are sourced from a classic
# data mirror because those keys (Art/Buttonpos/etc.) are stable and still map
# through UnitMetaData.  Map-local .w3u overrides ALWAYS win over this baseline.
#
# Advanced users may replace any downloaded file in native_data/ with the same
# file extracted from their own Warcraft III build; the loader is deliberately
# file-based for that reason.
REFORGED_SLK_BASE = "https://raw.githubusercontent.com/tdauth/wowr/main/wc3/wc3wowre/Units/"
CLASSIC_PROFILE_BASE = "https://raw.githubusercontent.com/WarRaft/War3.mpq/main/extract/Units/"
NATIVE_DATASET_VERSION = "wc3-object-studio-native-v4-reforged-complete-ui"

SLK_FILES = (
    "UnitMetaData.slk",
    "UnitData.slk",
    "unitUI.slk",
    "UnitBalance.slk",
    "UnitWeapons.slk",
    "UnitAbilities.slk",
)
CLASSIC_UNIT_UI_FILE = "unitUIClassic.slk"

PROFILE_FILES = (
    "HumanUnitFunc.txt", "HumanUnitStrings.txt",
    "OrcUnitFunc.txt", "OrcUnitStrings.txt",
    "UndeadUnitFunc.txt", "UndeadUnitStrings.txt",
    "NightElfUnitFunc.txt", "NightElfUnitStrings.txt",
    "NeutralUnitFunc.txt", "NeutralUnitStrings.txt",
    "CampaignUnitFunc.txt", "CampaignUnitStrings.txt",
)
REQUIRED_FILES = SLK_FILES + (CLASSIC_UNIT_UI_FILE,) + PROFILE_FILES

DOWNLOAD_URLS = {
    "UnitMetaData.slk": REFORGED_SLK_BASE + "UnitMetaData.slk",
    "UnitData.slk": REFORGED_SLK_BASE + "UnitData.slk",
    "unitUI.slk": REFORGED_SLK_BASE + "UnitUI.slk",
    "UnitBalance.slk": REFORGED_SLK_BASE + "UnitBalance.slk",
    "UnitWeapons.slk": REFORGED_SLK_BASE + "UnitWeapons.slk",
    "UnitAbilities.slk": REFORGED_SLK_BASE + "UnitAbilities.slk",
    CLASSIC_UNIT_UI_FILE: CLASSIC_PROFILE_BASE + "unitUI.slk",
}
DOWNLOAD_URLS.update({name: CLASSIC_PROFILE_BASE + name for name in PROFILE_FILES})

CATEGORY_LABELS = {
    "abil": "Habilidades",
    "art": "Arte",
    "combat": "Combate",
    "editor": "Geral",
    "hero": "Herói",
    "move": "Movimento",
    "path": "Movimento",
    "sound": "Arte",
    "stats": "Stats",
    "tech": "Geral",
    "text": "Geral",
}
CATEGORY_PREFIX = {
    "Habilidades": "Abilities",
    "Arte": "Art",
    "Combate": "Combat",
    "Editor": "Editor",
    "Herói": "Hero",
    "Movimento": "Movement",
    "Pathing": "Pathing",
    "Som": "Sound",
    "Stats": "Stats",
    "Tecnologia": "Techtree",
    "Texto": "Text",
}

# Labels that matter a lot in day-to-day Object Editor work.  Everything else
# falls back to a readable label generated from the SLK column name.
FRIENDLY_LABELS = {
    "uabi": "Abilities - Normal",
    "uabs": "Abilities - Normal Skin",
    "udaa": "Abilities - Default Active Ability",
    "uhab": "Abilities - Hero",
    "uhas": "Abilities - Hero Skin",
    "uani": "Art - Required Animation Names",
    "uaap": "Art - Required Animation Names - Attachments",
    "ualp": "Art - Required Attachment Link Names",
    "ubpr": "Art - Required Bone Names",
    "ubpx": "Art - Button Position (X)",
    "ubpy": "Art - Button Position (Y)",
    "uble": "Art - Animation - Blend Time (seconds)",
    "ucbs": "Art - Animation - Cast Backswing",
    "ucpt": "Art - Animation - Cast Point",
    "urun": "Art - Animation - Run Speed",
    "uwal": "Art - Animation - Walk Speed",
    "utcc": "Art - Allow Custom Team Color",
    "ucua": "Art - Caster Upgrade Art",
    "udtm": "Art - Death Time (seconds)",
    "uept": "Art - Elevation - Sample Points",
    "uerd": "Art - Elevation - Sample Radius",
    "ufrd": "Art - Fog of War - Sample Radius",
    "ushr": "Art - Has Water Shadow",
    "uico": "Art - Icon - Game Interface",
    "ussi": "Art - Icon - Score Screen",
    "umxp": "Art - Maximum Pitch Angle (degrees)",
    "umxr": "Art - Maximum Roll Angle (degrees)",
    "umdl": "Art - Model File",
    "uver": "Art - Model File - Extra Versions",
    "uocc": "Art - Occluder Height",
    "uori": "Art - Orientation Interpolation",
    "uimz": "Art - Projectile Impact - Z",
    "uisz": "Art - Projectile Impact - Z (Swimming)",
    "ulpx": "Art - Projectile Launch - X",
    "ulpy": "Art - Projectile Launch - Y",
    "ulpz": "Art - Projectile Launch - Z",
    "ulsz": "Art - Projectile Launch - Z (Swimming)",
    "uprw": "Art - Propulsion Window (degrees)",
    "usca": "Art - Scaling Value",
    "ussc": "Art - Selection Circle - Scale",
    "usew": "Art - Selection Circle - On Water",
    "uslz": "Art - Selection Circle - Height",
    "uclr": "Art - Tinting Color - Red",
    "uclg": "Art - Tinting Color - Green",
    "uclb": "Art - Tinting Color - Blue",
    "uhpm": "Stats - Hit Points Maximum (Base)",
    "umpm": "Stats - Mana Maximum",
    "umpi": "Stats - Mana Initial Amount",
    "uhpr": "Stats - Hit Point Regeneration Rate",
    "umpr": "Stats - Mana Regeneration",
    "umvs": "Movement - Speed Base",
    "umvt": "Movement - Type",
    "umvh": "Movement - Height",
    "umvf": "Movement - Height Minimum",
    "umvr": "Movement - Turn Rate",
    "ucol": "Pathing - Collision Size",
    "uacq": "Combat - Acquisition Range",
    "udef": "Combat - Defense Base",
    "udty": "Combat - Defense Type",
    "ua1b": "Combat - Attack 1 - Damage Base",
    "ua1d": "Combat - Attack 1 - Damage Number of Dice",
    "ua1s": "Combat - Attack 1 - Damage Sides per Die",
    "ua1c": "Combat - Attack 1 - Cooldown Time",
    "ua1r": "Combat - Attack 1 - Range",
    "ua1m": "Combat - Attack 1 - Projectile Art",
    "ua1t": "Combat - Attack 1 - Attack Type",
    "ua1w": "Combat - Attack 1 - Weapon Type",
    "ua1g": "Combat - Attack 1 - Targets Allowed",
    "ua1p": "Combat - Attack 1 - Animation Damage Point",
    "ua1q": "Combat - Attack 1 - Animation Backswing Point",
    "ua2b": "Combat - Attack 2 - Damage Base",
    "ua2c": "Combat - Attack 2 - Cooldown Time",
    "ua2r": "Combat - Attack 2 - Range",
    "unam": "Text - Name",
    "unsf": "Text - Editor Suffix",
    "utip": "Text - Tooltip - Basic",
    "utub": "Text - Tooltip - Extended",
    "upro": "Text - Proper Names",
    "upra": "Hero - Primary Attribute",
    "ustr": "Hero - Starting Strength",
    "ustp": "Hero - Strength per Level",
    "uagi": "Hero - Starting Agility",
    "uagp": "Hero - Agility per Level",
    "uint": "Hero - Starting Intelligence",
    "uinp": "Hero - Intelligence per Level",
    "ulev": "Stats - Level",
    "usid": "Stats - Sight Radius (Day)",
    "usin": "Stats - Sight Radius (Night)",
    "ufoo": "Stats - Food Cost",
    "ufma": "Stats - Food Produced",
    "ugol": "Stats - Gold Cost",
    "ulum": "Stats - Lumber Cost",
    "ubld": "Stats - Build Time",
}

INTISH_TYPES = {
    "int", "bool", "teamColor", "attackBits", "versionFlags", "deathType",
    "channelFlags", "silenceFlags", "spellDetail", "fullFlags", "unitClass",
}
REAL_TYPES = {"real"}
UNREAL_TYPES = {"unreal"}


def _type_code(meta_type: str) -> int:
    t = (meta_type or "").strip()
    if t in REAL_TYPES:
        return TYPE_REAL
    if t in UNREAL_TYPES:
        return TYPE_UNREAL
    if t in INTISH_TYPES:
        return TYPE_INT
    return TYPE_STRING


def _camel_words(value: str) -> str:
    if not value:
        return "Campo"
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    return " ".join(w[:1].upper() + w[1:] for w in value.split())


def _slk_value(raw: str):
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('""', '"')
    if not raw:
        return ""
    try:
        if any(ch in raw.lower() for ch in (".", "e")):
            return float(raw)
        return int(raw, 10)
    except Exception:
        return raw


def parse_slk(text: str) -> list[dict[str, object]]:
    """Parse the simple SYLK dialect Blizzard uses for Object Editor tables."""
    cells: dict[tuple[int, int], object] = {}
    cur_x = cur_y = 1
    max_x = max_y = 1
    for raw_line in text.splitlines():
        line = raw_line.strip("\r\n")
        if not line.startswith("C;"):
            continue
        # Blizzard values rarely contain semicolons; handle quoted semicolons
        # anyway so this parser remains safe for profile-ish string columns.
        parts=[]; buf=[]; quoted=False; i=0
        for ch in line[2:]:
            if ch == '"':
                quoted = not quoted
                buf.append(ch)
            elif ch == ';' and not quoted:
                parts.append(''.join(buf)); buf=[]
            else:
                buf.append(ch)
        parts.append(''.join(buf))
        value_marker = None
        for token in parts:
            if token.startswith("X") and token[1:].lstrip("-").isdigit():
                cur_x = int(token[1:]); max_x=max(max_x,cur_x)
            elif token.startswith("Y") and token[1:].lstrip("-").isdigit():
                cur_y = int(token[1:]); max_y=max(max_y,cur_y)
            elif token.startswith("K"):
                value_marker = token[1:]
        if value_marker is not None:
            cells[(cur_x,cur_y)] = _slk_value(value_marker)

    headers = {x: str(cells.get((x,1), "")).strip() for x in range(1,max_x+1)}
    rows=[]
    for y in range(2,max_y+1):
        row={}
        for x,name in headers.items():
            if name and (x,y) in cells:
                row[name]=cells[(x,y)]
        if row:
            rows.append(row)
    return rows


def parse_profile_text(text: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    section: str | None = None
    for raw in text.splitlines():
        line=raw.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue
        if line.startswith("[") and "]" in line:
            section=line[1:line.index("]")].strip()
            if len(section)==4:
                out.setdefault(section,{})
            else:
                section=None
            continue
        if section and "=" in line:
            key,value=line.split("=",1)
            out[section][key.strip()]=value.strip()
    return out


def ensure_native_data(data_dir: Path, timeout: float = 7.0, *, force: bool = False) -> tuple[list[str], list[str]]:
    """Download the baseline Object Editor data files when needed.

    A tiny dataset marker lets upgrades replace older/classic snapshots with
    the Reforged-era schema.  Nothing here is written into a map.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    marker = data_dir / ".dataset-version"
    current = marker.read_text(encoding="utf-8", errors="ignore").strip() if marker.exists() else ""
    refresh = force or (current != NATIVE_DATASET_VERSION)
    downloaded=[]; failed=[]
    for name in REQUIRED_FILES:
        path=data_dir/name
        if not refresh and path.exists() and path.stat().st_size > 100:
            continue
        url=DOWNLOAD_URLS[name]
        tmp=path.with_suffix(path.suffix+".part")
        try:
            LOGGER.info("Native data download: %s", url)
            req=urllib.request.Request(url,headers={"User-Agent":"WC3-Object-Studio/3.10.7"})
            with urllib.request.urlopen(req,timeout=timeout) as resp:
                data=resp.read()
            if len(data)<100:
                raise RuntimeError("download pequeno demais")
            tmp.write_bytes(data); tmp.replace(path); downloaded.append(name)
        except Exception as exc:
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            LOGGER.warning("Native data download failed for %s: %s", name, exc)
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
    missing=[name for name in REQUIRED_FILES if not (data_dir/name).exists() or (data_dir/name).stat().st_size <= 100]
    if not failed and not missing:
        marker.write_text(NATIVE_DATASET_VERSION,encoding="utf-8")
    return downloaded, failed


@dataclass
class NativeFieldMeta:
    field_id: str
    slk: str
    column: str
    index: int
    category: str
    sort: str
    meta_type: str
    use_hero: bool
    use_unit: bool
    use_building: bool
    can_be_empty: bool
    min_val: object | None = None
    max_val: object | None = None

    @property
    def type_code(self) -> int:
        return _type_code(self.meta_type)

    @property
    def label(self) -> str:
        if self.field_id in FRIENDLY_LABELS:
            return FRIENDLY_LABELS[self.field_id]
        cat = CATEGORY_LABELS.get(self.category, _camel_words(self.category))
        prefix = CATEGORY_PREFIX.get(cat, cat)
        return f"{prefix} - {_camel_words(self.column)}"


def _augment_reforged_metadata(metadata: dict[str, NativeFieldMeta]) -> None:
    """Add Reforged-only unit fields if an older metadata snapshot is used.

    Current Reforged UnitMetaData contains these rows, but keeping this guard
    makes the editor compatible with user-supplied older SLKs as well.
    """
    additions = {
        "uabs": NativeFieldMeta("uabs", "Profile", "abilSkinList", -1, "abil", "a2z10", "abilityList", True, True, True, True),
        "uhas": NativeFieldMeta("uhas", "Profile", "heroAbilSkinList", -1, "abil", "a2z11", "abilityList", True, False, False, True),
    }
    for fid, meta in additions.items():
        metadata.setdefault(fid, meta)


class NativeDataCatalog:
    def __init__(self, data_dir: Path, metadata: dict[str, NativeFieldMeta], tables: dict[str, dict[str, dict[str, object]]], profiles: dict[str, dict[str, str]] | None = None):
        self.data_dir=data_dir
        self.metadata=metadata
        self.tables=tables
        self.profiles=profiles or {}

    @property
    def ready(self) -> bool:
        return bool(self.metadata) and any(self.tables.values())

    @classmethod
    def load_default(cls, *, auto_fetch: bool = True) -> "NativeDataCatalog":
        root=Path(__file__).resolve().parent.parent
        data_dir=root/"native_data"
        if auto_fetch:
            ensure_native_data(data_dir)
        return cls.load(data_dir)

    @classmethod
    def load(cls, data_dir: Path) -> "NativeDataCatalog":
        metadata: dict[str,NativeFieldMeta]={}
        tables: dict[str,dict[str,dict[str,object]]]={}
        meta_path=data_dir/"UnitMetaData.slk"
        if meta_path.exists():
            try:
                for row in parse_slk(meta_path.read_text(encoding="utf-8",errors="replace")):
                    fid=str(row.get("ID", ""))
                    if len(fid)!=4:
                        continue
                    if not (int(row.get("useHero",0) or 0) or int(row.get("useUnit",0) or 0) or int(row.get("useBuilding",0) or 0)):
                        continue
                    metadata[fid]=NativeFieldMeta(
                        field_id=fid,
                        slk=str(row.get("slk", "")),
                        column=str(row.get("field", "")),
                        index=int(row.get("index",-1)) if row.get("index",-1) not in (None,"") else -1,
                        category=str(row.get("category", "editor")),
                        sort=str(row.get("sort", "zzzzz")),
                        meta_type=str(row.get("type", "string")),
                        use_hero=bool(int(row.get("useHero",0) or 0)),
                        use_unit=bool(int(row.get("useUnit",0) or 0)),
                        use_building=bool(int(row.get("useBuilding",0) or 0)),
                        can_be_empty=bool(int(row.get("canBeEmpty",0) or 0)),
                        min_val=row.get("minVal"), max_val=row.get("maxVal"),
                    )
            except Exception:
                LOGGER.exception("Failed parsing UnitMetaData.slk")
        _augment_reforged_metadata(metadata)
        for filename,table_name in (("UnitData.slk","UnitData"),("unitUI.slk","UnitUI"),("UnitBalance.slk","UnitBalance"),("UnitWeapons.slk","UnitWeapons"),("UnitAbilities.slk","UnitAbilities")):
            p=data_dir/filename
            if not p.exists():
                tables[table_name]={}; continue
            try:
                rows=parse_slk(p.read_text(encoding="utf-8",errors="replace"))
                indexed={}
                for row in rows:
                    # First SLK column is the rawcode; its header differs by table.
                    raw=None
                    for key in row:
                        val=row.get(key)
                        if isinstance(val,str) and len(val)==4 and key.lower() in {"unitid","unituiid","unitbalanceid","unitweaponsid","unitabilid","unitabilitiesid"}:
                            raw=val; break
                    if raw is None:
                        # Generic fallback: first 4-char string cell.
                        for val in row.values():
                            if isinstance(val,str) and len(val)==4:
                                raw=val; break
                    if raw:
                        indexed[raw]=row
                tables[table_name]=indexed
                LOGGER.info("Native table loaded %s: %d rows",table_name,len(indexed))
            except Exception:
                LOGGER.exception("Failed parsing native table %s",p)
                tables[table_name]={}

        # Reforged UnitUI.slk omits many classic Object Editor art columns
        # (file/scale/modelScale/tint/etc.). Fill only missing cells from the
        # classic UnitUI compatibility table; live Reforged data still wins.
        classic_ui = data_dir / CLASSIC_UNIT_UI_FILE
        if classic_ui.exists():
            try:
                classic_rows = parse_slk(classic_ui.read_text(encoding="utf-8", errors="replace"))
                unit_ui = tables.setdefault("UnitUI", {})
                filled = rows_added = 0
                for row in classic_rows:
                    raw = str(row.get("unitUIID") or row.get("unitID") or "").strip()
                    if len(raw) != 4:
                        continue
                    current = unit_ui.get(raw)
                    if current is None:
                        unit_ui[raw] = dict(row)
                        rows_added += 1
                        continue
                    existing = {str(k).casefold(): k for k in current}
                    for key, value in row.items():
                        if value in (None, ""):
                            continue
                        actual = existing.get(str(key).casefold())
                        if actual is None or current.get(actual) in (None, ""):
                            current[key] = value
                            existing[str(key).casefold()] = key
                            filled += 1
                LOGGER.info("Classic UnitUI compatibility merge: rows_added=%d cells_filled=%d", rows_added, filled)
            except Exception:
                LOGGER.exception("Failed merging classic UnitUI compatibility data")

        profiles: dict[str, dict[str, str]] = {}
        for filename in PROFILE_FILES:
            p=data_dir/filename
            if not p.exists():
                continue
            try:
                parsed=parse_profile_text(p.read_text(encoding="utf-8",errors="replace"))
                for raw,values in parsed.items():
                    profiles.setdefault(raw,{}).update(values)
            except Exception:
                LOGGER.exception("Failed parsing native profile %s",p)
        LOGGER.info("Native Object Editor catalog: fields=%d tables=%d profiles=%d ready=%s dir=%s",len(metadata),sum(len(x) for x in tables.values()),len(profiles),bool(metadata and any(tables.values())),data_dir)
        return cls(data_dir,metadata,tables,profiles)

    def field_defs(self, *, hero: bool | None = None) -> dict[str, FieldDef]:
        out={}
        metas=list(self.metadata.values())
        metas.sort(key=lambda m:(CATEGORY_LABELS.get(m.category,m.category),m.sort,m.label,m.field_id))
        for meta in metas:
            if hero is True and not (meta.use_hero or meta.use_unit):
                continue
            if hero is False and not (meta.use_unit or meta.use_building):
                continue
            cat=CATEGORY_LABELS.get(meta.category,_camel_words(meta.category))
            # Profile fields are still valuable to expose/edit, even if their
            # inherited base value is not present in the SLK baseline.
            help_parts=[f"{meta.field_id} · {meta.slk}.{meta.column}"]
            if meta.min_val not in (None,""): help_parts.append(f"min {meta.min_val}")
            if meta.max_val not in (None,""): help_parts.append(f"max {meta.max_val}")
            out[meta.field_id]=FieldDef(meta.field_id,meta.label,meta.type_code,cat,False," · ".join(help_parts),meta.meta_type,meta.slk,meta.column,meta.sort)
        return out

    def resolve_unit(self, rawcode: str, field_id: str) -> tuple[Modification | None, str]:
        meta=self.metadata.get(field_id)
        if meta is None:
            return None,f"base nativa {rawcode} · campo sem metadata"

        def ci_value(row, column):
            if not row:
                return None, None
            key=next((k for k in row if str(k).casefold()==str(column).casefold()),None)
            return (row.get(key), key) if key is not None else (None, None)

        source_table=meta.slk
        source_column=meta.column
        value=None

        if meta.slk.lower()=="profile":
            profile_row=self.profiles.get(rawcode,{})
            # Reforged UnitSkin frequently splits old profile values into
            # ``field:hd`` / ``field:sd`` (modelScale, file, scale, tint...).
            # Prefer the neutral value when present, then HD (the app default),
            # then SD.  This fills the Object Editor form instead of showing an
            # empty inherited value even though CASC has the data.
            key=None
            for candidate in (
                meta.column,
                f"{meta.column}:hd", f"{meta.column}_hd",
                f"{meta.column}:sd", f"{meta.column}_sd",
            ):
                value,key=ci_value(profile_row,candidate)
                if key is not None and str(value).strip() not in {"", "-", "_"}:
                    source_column=str(key)
                    break
                key=None
            if key is None:
                value,key=ci_value(self.tables.get("UnitUI",{}).get(rawcode,{}),meta.column)
                if key is not None:
                    source_table="UnitUI"
                    source_column=str(key)
            if key is None:
                return None,f"base nativa {rawcode} · Profile.{meta.column} sem valor"
        else:
            table=self.tables.get(meta.slk,{})
            row=table.get(rawcode)
            if not row:
                return None,f"base nativa {rawcode} · {meta.slk} sem registro"
            value,key=ci_value(row,meta.column)
            if key is None:
                return None,f"base nativa {rawcode} · {meta.slk}.{meta.column} sem valor"
            source_column=str(key)

        if meta.index >= 0:
            parts=[x.strip() for x in str(value).split(",")]
            if meta.index >= len(parts):
                return None,f"base nativa {rawcode} · {source_table}.{source_column}[{meta.index}] sem valor"
            value=parts[meta.index]
        source=f"base nativa {rawcode} · {source_table}.{source_column}" + (f"[{meta.index}]" if meta.index>=0 else "")

        raw_text=str(value).strip() if value is not None else ""
        if raw_text in {"-", "_"}:
            if meta.type_code==TYPE_STRING:
                value=""
            else:
                return None,source+" sem valor"
        try:
            if meta.type_code==TYPE_INT:
                if isinstance(value,str) and value.lower() in {"true","false"}:
                    value=1 if value.lower()=="true" else 0
                value=int(float(value))
            elif meta.type_code in (TYPE_REAL,TYPE_UNREAL):
                value=float(value)
            else:
                value=str(value)
        except Exception:
            return None,source+f" inválido ({value!r})"
        return Modification(field_id,meta.type_code,value),source

