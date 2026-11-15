from __future__ import annotations

import logging
import urllib.request
from pathlib import Path
from typing import Iterable

from .casc import CascStorage
from .native_data import CLASSIC_PROFILE_BASE, parse_profile_text, parse_slk

LOGGER = logging.getLogger("wc3studio")

# Ability object text/art is split across several Blizzard profile files.
# Item abilities are included as well because they are normal ability rawcodes
# and can be inherited by custom map objects.
ABILITY_PROFILE_FILES = (
    "HumanAbilityFunc.txt", "HumanAbilityStrings.txt",
    "OrcAbilityFunc.txt", "OrcAbilityStrings.txt",
    "UndeadAbilityFunc.txt", "UndeadAbilityStrings.txt",
    "NightElfAbilityFunc.txt", "NightElfAbilityStrings.txt",
    "NeutralAbilityFunc.txt", "NeutralAbilityStrings.txt",
    "CampaignAbilityFunc.txt", "CampaignAbilityStrings.txt",
    "CommonAbilityFunc.txt", "CommonAbilityStrings.txt",
    "ItemAbilityFunc.txt", "ItemAbilityStrings.txt",
)
ABILITY_SKIN_FILES = ("AbilitySkin.txt",)
ABILITY_FALLBACK_FILES = ABILITY_PROFILE_FILES

FIELD_TO_PROFILE = {
    "acat": ("CasterArt", "Casterart"),
    "atat": ("TargetArt", "Targetart"),
    "asat": ("SpecialArt", "Specialart"),
    "aeat": ("EffectArt", "Effectart"),
    "aaea": ("Areaeffectart", "AreaEffectArt", "Areaart"),
    "amat": ("MissileArt", "Missileart"),
    "aani": ("Animnames", "AnimNames"),
    "aart": ("Art", "Art:hd", "Art_HD", "ArtHD", "Art:sd", "Art_SD", "ArtSD"),
    "anam": ("Name",),
    "abuf": ("BuffID", "BuffId"),
    "aeff": ("EfctID", "EffectID", "EffectId"),
}

LIST_VALUE_FIELDS = {"aani", "acat", "atat", "asat", "aeat", "aaea", "amat", "abuf", "aeff", "atar"}

VISUAL_FIELDS = (
    ("acat", "Caster Art", "caster"),
    ("atat", "Target Art", "target"),
    ("asat", "Special Art", "caster"),
    ("aeat", "Effect Art", "target"),
    ("aaea", "Area Effect Art", "area"),
    ("amat", "Missile Art", "missile"),
)

_PROFILE_CACHE: dict[str, dict[str, dict[str, str]]] = {}

# Object Editor field IDs -> keys used by Blizzard AbilityData/Func/Strings.
NATIVE_FIELD_KEYS = {
    "anam": ("Name",),
    "aart": ("Art", "Art:hd", "Art_HD", "ArtHD", "Art:sd", "Art_SD", "ArtSD"),
    "alev": ("levels", "Levels"),
    "arlv": ("reqLevel", "ReqLevel"),
    "alsk": ("levelSkip", "LevelSkip"),
    "aher": ("hero", "Hero"),
    "aite": ("item", "Item"),
    "arac": ("race", "Race"),
    "ahky": ("Hotkey",),
    "atp1": ("Tip", "Tip1"),
    "aub1": ("Ubertip", "UberTip", "Ubertip1"),
    "aret": ("Researchtip", "ResearchTip"),
    "arut": ("Researchubertip", "ResearchUbertip"),
    "arhk": ("Researchhotkey", "ResearchHotkey"),
    "acdn": ("Cool", "Cooldown"),
    "amcs": ("Cost", "ManaCost"),
    "aran": ("Rng", "Range", "CastRange"),
    "aare": ("Area", "AreaOfEffect"),
    "adur": ("Dur", "Duration"),
    "ahdu": ("HeroDur", "HeroDuration"),
    "atar": ("targs", "Targets", "TargetsAllowed"),
    "aord": ("Order", "OrderString"),
    "aani": ("Animnames", "AnimNames"),
    "acat": ("CasterArt", "Casterart"),
    "atat": ("TargetArt", "Targetart"),
    "asat": ("SpecialArt", "Specialart"),
    "aeat": ("EffectArt", "Effectart"),
    "aaea": ("Areaeffectart", "AreaEffectArt", "Areaart"),
    "amat": ("MissileArt", "Missileart"),
    "abuf": ("BuffID", "BuffId"),
    "aeff": ("EfctID", "EffectID", "EffectId"),
}


def _native_data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "native_data"


def ensure_ability_fallback_profiles(data_dir: Path | None = None, timeout: float = 10.0) -> tuple[list[str], list[str]]:
    """Ensure offline Blizzard ability Func/Strings fallback files.

    Downloads run concurrently so an offline machine pays roughly one timeout,
    not one timeout per file. Live CASC remains the preferred source.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    folder = Path(data_dir or _native_data_dir())
    folder.mkdir(parents=True, exist_ok=True)
    missing = [
        name for name in ABILITY_FALLBACK_FILES
        if not (folder / name).exists() or (folder / name).stat().st_size <= 100
    ]
    downloaded: list[str] = []
    failed: list[str] = []
    if not missing:
        return downloaded, failed

    def fetch(name: str) -> tuple[str, str | None]:
        path = folder / name
        url = CLASSIC_PROFILE_BASE + name
        tmp = path.with_suffix(path.suffix + ".part")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WC3-Object-Studio/3.10.7"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                data = response.read()
            if len(data) < 100:
                raise RuntimeError("download pequeno demais")
            tmp.write_bytes(data)
            tmp.replace(path)
            return name, None
        except Exception as exc:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return name, f"{name}: {type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=min(8, len(missing))) as pool:
        futures=[pool.submit(fetch,name) for name in missing]
        for future in as_completed(futures):
            name,error=future.result()
            if error:
                failed.append(error)
            else:
                downloaded.append(name)
    downloaded.sort(); failed.sort()
    return downloaded, failed


def _split_level_value(value: str, level: int) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    parts = [x.strip() for x in value.split(",")]
    if level and len(parts) > 1:
        idx = max(0, min(len(parts) - 1, int(level) - 1))
        return parts[idx]
    return value


def _ci_get(row: dict[str, str], keys: Iterable[str]) -> str:
    folded = {str(k).casefold(): v for k, v in row.items()}
    for key in keys:
        if key.casefold() not in folded:
            continue
        value = str(folded[key.casefold()]).strip()
        # Skin/profile files sometimes keep an empty HD key next to a valid SD
        # fallback.  Keep searching aliases until we find a useful value.
        if value != "":
            return value
    return ""


def profile_field_value(row: dict[str, str], field_id: str, level: int = 0) -> tuple[str, str]:
    if not row:
        return "", ""
    if field_id in {"abpx", "abpy"}:
        pos = _ci_get(row, ("Buttonpos", "ButtonPos", "ButtonPosition"))
        parts = [x.strip() for x in pos.split(",")] if pos else []
        idx = 0 if field_id == "abpx" else 1
        value = parts[idx] if idx < len(parts) else ""
        return value, "Buttonpos"
    keys = NATIVE_FIELD_KEYS.get(field_id, (field_id,))
    if level and int(level) > 0:
        level_keys = tuple(f"{key}{int(level)}" for key in keys)
        value = _ci_get(row, level_keys)
        if value != "":
            return value, level_keys[0]
    value = _ci_get(row, keys)
    if value != "":
        if field_id == "aart":
            # Ability command-button art is a single visual. Skin files may
            # still serialize fallback candidates as a comma list; the game
            # uses the first non-empty entry rather than treating them as
            # ability levels.
            first = next((x.strip().strip('"') for x in value.split(",") if x.strip()), "")
            return first, keys[0]
        if field_id in LIST_VALUE_FIELDS:
            return value, keys[0]
        return _split_level_value(value, level), keys[0]
    level1_keys = tuple(f"{key}1" for key in keys)
    value = _ci_get(row, level1_keys)
    return value, (level1_keys[0] if value != "" else keys[0])


def _clean_display_text(value: str) -> str:
    value = str(value or "").strip().strip('"')
    # Remove common Warcraft color/newline markup without trying to render the
    # entire tooltip mini-language in the object tree.
    import re
    value = re.sub(r"\|c[0-9a-fA-F]{8}", "", value)
    value = value.replace("|r", "").replace("|n", " ")
    return " ".join(value.split())


def profile_display_name(row: dict[str, str], rawcode: str, resolver=None) -> str:
    """Return the best human-readable ability name available.

    Reforged keeps many Object Editor-only abilities as WESTRING tokens.  The
    old browser rejected those tokens and displayed only Aes2/AHh1/etc.  When
    a WorldEditStrings resolver is supplied, resolve those tokens before
    falling back to research/tooltips or the rawcode.
    """
    for keys in (("Name",), ("Researchtip", "ResearchTip"), ("Tip", "Tip1"), ("EditorName", "Editorname", "Comment", "Comments")):
        value = _ci_get(row, keys)
        if not value:
            continue
        value = value.split(",")[0].strip()
        resolved = resolver(value) if resolver is not None else value
        resolved = _clean_display_text(resolved)
        if resolved and not resolved.upper().startswith("WESTRING_") and not resolved.startswith("$"):
            return resolved
    return rawcode


def _merge_profile_file(merged: dict[str, dict[str, str]], text: str) -> int:
    parsed = parse_profile_text(text)
    # Blizzard has shipped a handful of localized sections whose rawcode case
    # differs from AbilityData.slk (for example Ansp/ANsp). Rawcodes are
    # normally case-sensitive, so only repair the mismatch when there is one
    # unambiguous pre-existing case-insensitive match.
    folded_existing: dict[str, list[str]] = {}
    for existing in merged:
        folded_existing.setdefault(existing.casefold(), []).append(existing)
    for raw, row in parsed.items():
        target = raw
        if raw not in merged:
            matches = folded_existing.get(raw.casefold(), [])
            if len(matches) == 1:
                target = matches[0]
        target_row = merged.setdefault(target, {})
        for field, value in row.items():
            # A few localized Reforged editor rows use the rawcode itself as a
            # placeholder Name (for example ACnr=ACnr). Do not let that erase a
            # useful Blizzard baseline name that was already merged offline.
            if str(field).casefold() == "name":
                incoming = str(value or "").strip().strip('"')
                existing_name = next((str(v).strip() for k, v in target_row.items() if str(k).casefold() == "name" and str(v).strip()), "")
                if incoming.casefold() == target.casefold() and existing_name and existing_name.casefold() != target.casefold():
                    continue
            target_row[field] = value
        folded_existing.setdefault(target.casefold(), [])
        if target not in folded_existing[target.casefold()]:
            folded_existing[target.casefold()].append(target)
    return len(parsed)


def _read_local_profile(filename: str) -> tuple[str, bytes] | None:
    path = _native_data_dir() / filename
    if path.is_file() and path.stat().st_size > 100:
        try:
            return f"offline:{filename}", path.read_bytes()
        except Exception:
            LOGGER.debug("Failed reading offline ability profile %s", path, exc_info=True)
    return None


def _profile_refs(filename: str) -> list[str]:
    if filename.casefold().endswith("strings.txt"):
        # Reforged keeps locale files under war3.w3mod:_locales\<lang>.w3mod.
        # Some extractors expose flattened aliases such as enus.w3mod:, but
        # CascLib itself commonly needs the full nested VFS path.  Try the
        # installed-user locale first (ptBR for this build), then enUS, then
        # compatibility aliases used by older Warcraft/CascLib versions.
        return [
            f"war3.w3mod:_locales\\ptbr.w3mod:units/{filename}",
            f"war3.w3mod:_locales\\enus.w3mod:units/{filename}",
            f"war3.w3mod:_hd.w3mod:_locales\\ptbr.w3mod:units/{filename}",
            f"war3.w3mod:_hd.w3mod:_locales\\enus.w3mod:units/{filename}",
            f"ptbr.w3mod:units/{filename}",
            f"enus.w3mod:units/{filename}",
            f"_locale_.w3mod:units/{filename}",
            f"war3.w3mod:units/{filename}",
            f"units/{filename}",
        ]
    return [f"war3.w3mod:units/{filename}", f"units/{filename}"]


def load_profiles(casc: CascStorage) -> dict[str, dict[str, str]]:
    cache_key = str(casc.install_path).casefold()
    if cache_key in _PROFILE_CACHE:
        return _PROFILE_CACHE[cache_key]
    merged: dict[str, dict[str, str]] = {}

    try:
        actual, data = casc.read_first((
            "war3.w3mod:units/AbilityData.slk", "units/AbilityData.slk",
            "war3.w3mod:Units/AbilityData.slk", "Units/AbilityData.slk",
        ))
        rows = parse_slk(data.decode("utf-8-sig", "replace"))
        count = 0
        for row in rows:
            folded = {str(k).casefold(): v for k, v in row.items()}
            raw = str(folded.get("alias") or folded.get("id") or "").strip()
            if len(raw) == 4:
                target = merged.setdefault(raw, {})
                target.update({str(k): str(v) for k, v in row.items() if v is not None})
                # Auxiliary Bxxx/Xxxx sections also live in AbilityFunc files but
                # are Buff/Effect profiles, not actual Ability objects. Keep them
                # available for Spell Lab while marking only AbilityData rows as
                # browseable/editable native spells.
                target["__ability_object__"] = "1"
                count += 1
        LOGGER.info("CASC AbilityData loaded: %s rows=%d", actual, count)
    except Exception:
        LOGGER.warning("CASC AbilityData.slk unavailable; native gameplay fields may be incomplete", exc_info=True)

    for filename in ABILITY_PROFILE_FILES:
        # The localized Reforged files exposed by CASC are not guaranteed to be
        # complete. A few editor-only/native variants (ACnr is a common example)
        # can be absent even though the bundled Blizzard baseline has a perfectly
        # good Name/EditorSuffix. Merge the offline Blizzard profile first as a
        # completeness baseline, then overlay the installed CASC locale/build.
        # This keeps current translated values authoritative without falling back
        # to a rawcode merely because one localized section was omitted.
        local = _read_local_profile(filename)
        if local is not None:
            local_source, local_data = local
            try:
                baseline_count = _merge_profile_file(merged, local_data.decode("utf-8-sig", "replace"))
                LOGGER.debug("Ability profile baseline merged: %s sections=%d", local_source, baseline_count)
            except Exception:
                LOGGER.warning("Failed parsing offline ability profile %s", filename, exc_info=True)

        try:
            source, data = casc.read_first(_profile_refs(filename))
        except Exception:
            if local is None:
                LOGGER.debug("CASC/offline ability profile missing: %s", filename)
            continue
        try:
            count = _merge_profile_file(merged, data.decode("utf-8-sig", "replace"))
            LOGGER.info("Ability profile loaded: %s sections=%d", source, count)
        except Exception:
            LOGGER.warning("Failed parsing ability profile %s", filename, exc_info=True)

    # Reforged AbilitySkin is an overlay for command-button art.  Load it last so
    # its Art path wins over the classic/base Func path when present.
    for filename in ABILITY_SKIN_FILES:
        try:
            source, data = casc.read_first((
                f"war3.w3mod:units/{filename}",
                f"war3.w3mod:_hd.w3mod:units/{filename}",
                f"units/{filename}",
            ))
        except Exception:
            LOGGER.debug("CASC ability skin missing: %s", filename)
            continue
        try:
            count = _merge_profile_file(merged, data.decode("utf-8-sig", "replace"))
            LOGGER.info("CASC ability skin loaded: %s sections=%d", source, count)
        except Exception:
            LOGGER.warning("Failed parsing CASC ability skin %s", filename, exc_info=True)

    _PROFILE_CACHE[cache_key] = merged
    object_rows = [raw for raw, row in merged.items() if row.get("__ability_object__") == "1"]
    named_rows = sum(1 for raw in object_rows if profile_display_name(merged.get(raw, {}), raw) != raw)
    icon_rows = sum(1 for raw in object_rows if profile_field_value(merged.get(raw, {}), "aart", 0)[0])
    LOGGER.info(
        "CASC ability profiles ready: objects=%d auxiliary_profiles=%d named=%d icons=%d total_sections=%d",
        len(object_rows), max(0, len(merged)-len(object_rows)), named_rows, icon_rows, len(merged)
    )
    if object_rows and named_rows < max(10, len(object_rows)//4):
        LOGGER.warning(
            "Native ability names look incomplete (%d/%d). Check *_AbilityStrings locale VFS paths.",
            named_rows, len(object_rows)
        )
    return merged


def _split_rawcode_list(value: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in str(value or "").replace(";", ",").split(","):
        raw = item.strip().strip('"')
        if len(raw) != 4 or raw in seen or raw in {"_", "null", "NONE"}:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def _related_profile_ids(row: dict[str, str], level: int = 1) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field_id, label in (("abuf", "Buff"), ("aeff", "Effect")):
        value, _key = profile_field_value(row, field_id, max(1, int(level or 1)))
        for raw in _split_rawcode_list(value):
            if raw not in seen:
                seen.add(raw)
                out.append((raw, label))
    return out


def profile_visual_bundle(profiles: dict[str, dict[str, str]], rawcode: str, level: int = 1, resolver=None) -> dict[str, object]:
    """Return the complete static visual definition for one native ability.

    Warcraft ability art is not always stored on the ability section itself.
    BuffID/EfctID can point at Bxxx/Xxxx profile sections that contain the
    target/effect model.  Spell Lab needs those related rows to resemble what the
    engine creates in-game.
    """
    row = profiles.get(rawcode, {})
    name = profile_display_name(row, rawcode, resolver=resolver)
    icon, _ = profile_field_value(row, "aart", 0)
    anim, _ = profile_field_value(row, "aani", 0)
    effects: list[dict[str, str]] = []
    seen_effects: set[tuple[str, str]] = set()

    def collect(source_id: str, source_row: dict[str, str], relation: str) -> None:
        for field_id, label, placement in VISUAL_FIELDS:
            value, _key = profile_field_value(source_row, field_id, max(1, int(level or 1)))
            if not value:
                # Most Func art is not level-suffixed; profile_field_value already
                # checks generic keys, this branch is mainly readability.
                continue
            for ref in [x.strip().strip('"') for x in value.replace(";", ",").split(",") if x.strip()]:
                key = (placement, ref.casefold())
                if key in seen_effects:
                    continue
                seen_effects.add(key)
                effects.append({
                    "field": field_id,
                    "label": label if relation == "ability" else f"{relation} {source_id} · {label}",
                    "placement": placement,
                    "archive_ref": ref,
                    "source": f"CASC/profile {source_id}",
                    "profile_id": source_id,
                    "relation": relation,
                })

    collect(rawcode, row, "ability")
    for related_id, relation in _related_profile_ids(row, level=level):
        related = profiles.get(related_id, {})
        if related:
            collect(related_id, related, relation)

    return {
        "rawcode": rawcode,
        "name": name,
        "icon": icon,
        "cast_animation": anim,
        "effects": effects,
        "related_ids": [raw for raw, _kind in _related_profile_ids(row, level=level)],
    }


def resolve_profile_field(casc: CascStorage, rawcode: str, field_id: str) -> tuple[str, str]:
    row = load_profiles(casc).get(rawcode, {})
    keys = FIELD_TO_PROFILE.get(field_id, (field_id,))
    value = _ci_get(row, keys)
    return value, (f"CASC {rawcode} · {keys[0]}" if value else f"CASC {rawcode} · sem {keys[0]}")


def resolve_ability_profile(casc: CascStorage, rawcode: str) -> dict[str, str]:
    return dict(load_profiles(casc).get(rawcode, {}))


def resolve_ability_visual_bundle(casc: CascStorage, rawcode: str, level: int = 1) -> dict[str, object]:
    return profile_visual_bundle(load_profiles(casc), rawcode, level=level)
