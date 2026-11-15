from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import PurePosixPath

GENERIC_ABILITIES = {"AInv"}
ICON_EXTENSIONS = {".blp", ".tga", ".dds", ".png", ".jpg", ".jpeg"}


def _mods(rec) -> dict[str, object]:
    return {m.field_id: m.value for m in getattr(rec, "modifications", [])}


def _ability_set(rec_or_mods) -> set[str]:
    if isinstance(rec_or_mods, list):
        vals = {m.field_id: m.value for m in rec_or_mods}
    else:
        vals = _mods(rec_or_mods)
    out: set[str] = set()
    for field in ("uabi", "uhab"):
        raw = str(vals.get(field, "") or "")
        for item in raw.split(","):
            item = item.strip()
            if len(item) == 4 and item not in GENERIC_ABILITIES:
                out.add(item)
    return out


def _ability_families(abilities: set[str]) -> set[str]:
    out = set()
    for raw in abilities:
        # X123-style quest/raw helper IDs are intentionally ignored: they are
        # too generic to identify a character.  FJ06 -> FJ, YMEV -> YM, etc.
        if raw.startswith("X"):
            continue
        m = re.match(r"([A-Za-z]{2})", raw)
        if m:
            out.add(m.group(1).upper())
    return out


def _name_tokens(value: str) -> set[str]:
    stop = {
        "the", "and", "with", "from", "hero", "unit", "custom", "form",
        "gravity", "admiral", "permanent", "quest", "point", "classic",
    }
    return {
        x for x in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(x) >= 4 and x not in stop and not x.isdigit()
    }


@dataclass(frozen=True)
class VisualMatch:
    object_id: str
    field_id: str
    value: str
    score: float
    reason: str


def best_sibling_visual(records, selected_id: str, selected_base: str, selected_mods, field_id: str, selected_name: str = "") -> VisualMatch | None:
    """Find a likely visual donor among sibling custom units.

    OPWU has quest/transformation records that intentionally reuse a native base
    and omit ``umdl``/``uico``.  The matching original character record often
    carries the actual imported model.  This resolver is display-only: it never
    writes the inferred field back to object-data.
    """
    selected_abilities = _ability_set(selected_mods)
    selected_families = _ability_families(selected_abilities)
    selected_vals = {m.field_id: m.value for m in selected_mods}
    try:
        selected_scale = float(selected_vals.get("usca"))
    except (TypeError, ValueError):
        selected_scale = None
    selected_tokens = _name_tokens(selected_name)

    best: VisualMatch | None = None
    for table, oid, base, rec in records:
        if oid == selected_id or table != "custom" or base != selected_base:
            continue
        vals = _mods(rec)
        value = str(vals.get(field_id, "") or "").strip()
        if not value:
            continue

        score = 40.0  # same native base
        reasons = [f"mesma base {selected_base}"]
        abilities = _ability_set(rec)
        exact = selected_abilities & abilities
        if exact:
            score += 110.0 * len(exact)
            reasons.append("ability " + ",".join(sorted(exact)))
        fam = selected_families & _ability_families(abilities)
        if fam:
            score += 35.0 * len(fam)
            reasons.append("família " + ",".join(sorted(fam)))

        try:
            cand_scale = float(vals.get("usca"))
        except (TypeError, ValueError):
            cand_scale = None
        if selected_scale is not None and cand_scale is not None and abs(selected_scale - cand_scale) <= 0.02:
            score += 25.0
            reasons.append("mesma escala")

        cand_name = str(vals.get("unam", "") or "")
        overlap = selected_tokens & _name_tokens(cand_name)
        if overlap:
            score += 45.0 * len(overlap)
            reasons.append("nome " + ",".join(sorted(overlap)))

        # Require more than "same base" alone.  This prevents e.g. unrelated
        # Hmkg-derived heroes from donating visuals just because they share a
        # Blizzard template.
        if score < 90.0:
            continue
        match = VisualMatch(oid, field_id, value, score, "; ".join(reasons))
        if best is None or match.score > best.score:
            best = match
    return best


def infer_icon_import(import_paths: list[str], hints: list[str], *, purpose: str = "generic") -> tuple[str, float, str] | None:
    """Infer a CommandButtons icon from imported asset names.

    Used only when the selected object has no explicit icon override. It is
    conservative and display-only. ``purpose="unit"`` additionally rejects
    spell-looking BTN names so a hero portrait is not replaced by one of that
    hero's skill icons. Disabled icons (DISBTN) are deprioritized.
    """
    hint_tokens: set[str] = set()
    for hint in hints:
        hint_tokens |= _name_tokens(hint.replace("\\", "/"))
        stem = PurePosixPath(hint.replace("\\", "/")).stem
        hint_tokens |= _name_tokens(stem)
    if not hint_tokens:
        return None

    best = None
    texture_words = {
        "body", "cloak", "weapon", "hair", "face", "skin", "cloth",
        "armor", "arm", "leg", "head", "eye", "mouth", "diffuse",
        "albedo", "normal", "roughness", "metallic", "emissive", "mask",
    }
    texture_suffixes = ("_d", "_n", "_orm", "_r", "_m", "_e", "_mask")
    for path in import_paths:
        norm = path.replace("\\", "/")
        lower = norm.casefold()
        suffix = PurePosixPath(norm).suffix.casefold()
        if suffix not in ICON_EXTENSIONS:
            continue
        stem = PurePosixPath(norm).stem.casefold()

        # A character texture can match the same name tokens as its icon.  The
        # previous heuristic therefore picked lssho001_body_d.blp for Fujitora.
        # Only files that actually look like Warcraft command-button assets are
        # eligible for automatic icon inference.
        in_commandbuttons = "replaceabletextures/commandbuttons" in lower
        btn_named = stem.startswith("btn") or stem.startswith("disbtn")
        if not (in_commandbuttons or btn_named):
            continue
        if any(stem.endswith(x) for x in texture_suffixes) and not btn_named:
            continue
        if any(word in stem for word in texture_words) and not btn_named:
            continue

        score = 0.0
        reasons = []
        if in_commandbuttons:
            score += 120
            reasons.append("CommandButtons")
        if stem.startswith("disbtn"):
            score -= 90
            reasons.append("DISBTN")
        elif stem.startswith("pasbtn"):
            score -= 15
            reasons.append("PASBTN")
        elif stem.startswith("btn"):
            score += 90
            reasons.append("BTN")
        for tok in hint_tokens:
            if tok in lower:
                score += 140
                reasons.append(tok)

        if purpose == "unit":
            # Unit/hero portrait inference must be much stricter than generic
            # command-button inference.  A map can contain dozens of skill icons
            # named BTN_OPWU_fujitora_01_gravity_press.blp; matching the character
            # token alone would incorrectly display Gravity Press as Fujitora's
            # portrait. Prefer compact names such as BTNFujitora/BTNHeroFujitora.
            core = re.sub(r"^(?:disbtn|pasbtn|btn)", "", stem, flags=re.I)
            core_tokens = {x for x in re.findall(r"[a-z0-9]+", core) if x not in {"opwu", "hero", "unit", "icon", "portrait", "classic", "hd", "sd"}}
            matched = {tok for tok in hint_tokens if tok in core.casefold()}
            extras = {x for x in core_tokens if x not in matched}
            digitish = any(any(ch.isdigit() for ch in x) for x in extras)
            semantic_extras = {x for x in extras if not x.isdigit()}
            if matched and not extras:
                score += 260
                reasons.append("portrait-name")
            elif matched and not semantic_extras:
                # BTN_Character_01 is a common portrait naming convention.
                score += 120
                reasons.append("portrait-index")
            elif matched and len(semantic_extras) <= 1 and not digitish:
                score += 50
            else:
                score -= 220
                reasons.append("skill-like")
            # Number + semantic suffixes (01_gravity_press) are characteristic
            # of skill packs and must not become the unit portrait.
            if matched and semantic_extras and (digitish or len(semantic_extras) >= 2):
                score -= 220

        threshold = 300 if purpose == "unit" else 220
        if score < threshold:
            continue
        candidate = (path, score, ", ".join(dict.fromkeys(reasons)))
        if best is None or candidate[1] > best[1]:
            best = candidate
    return best


def _icon_core(path: str) -> str:
    """Canonical basename used to pair BTN/DISBTN/PASBTN variants."""
    stem = PurePosixPath(path.replace("\\", "/")).stem.casefold()
    stem = re.sub(r"^(?:disbtn|pasbtn|btn)", "", stem, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "", stem)


def related_icon_variants(import_paths: list[str], primary_path: str) -> dict[str, str]:
    """Return Warcraft command-button variants related to ``primary_path``.

    Keys are ``normal``, ``disabled`` and ``passive`` when matching assets are
    present in the map imports. Matching is based on the BTN/DISBTN/PASBTN core
    basename and intentionally does not guess unrelated skill icons.
    """
    core = _icon_core(primary_path)
    if not core:
        return {}
    out: dict[str, tuple[int, str]] = {}
    for path in import_paths:
        norm = path.replace("\\", "/")
        suffix = PurePosixPath(norm).suffix.casefold()
        if suffix not in ICON_EXTENSIONS or _icon_core(norm) != core:
            continue
        stem = PurePosixPath(norm).stem.casefold()
        lower = norm.casefold()
        if stem.startswith("disbtn") or "commandbuttonsdisabled" in lower:
            kind = "disabled"
            score = 300 if stem.startswith("disbtn") else 200
        elif stem.startswith("pasbtn") or "passivebuttons" in lower:
            kind = "passive"
            score = 300 if stem.startswith("pasbtn") else 200
        elif stem.startswith("btn") or "commandbuttons" in lower:
            kind = "normal"
            score = 300 if stem.startswith("btn") else 200
        else:
            continue
        if path.casefold() == primary_path.casefold():
            score += 1000
        prev = out.get(kind)
        if prev is None or score > prev[0]:
            out[kind] = (score, path)
    # Keep an explicit primary path even if it is not listed in war3map.imp.
    pnorm = primary_path.replace("\\", "/").casefold()
    pstem = PurePosixPath(pnorm).stem
    if pstem.startswith("disbtn") or "commandbuttonsdisabled" in pnorm:
        out.setdefault("disabled", (999, primary_path))
    elif pstem.startswith("pasbtn") or "passivebuttons" in pnorm:
        out.setdefault("passive", (999, primary_path))
    else:
        out.setdefault("normal", (999, primary_path))
    return {k: v[1] for k, v in out.items()}
