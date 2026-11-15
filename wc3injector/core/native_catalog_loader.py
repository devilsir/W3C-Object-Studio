from __future__ import annotations

from dataclasses import dataclass

from ..ability_native import load_profiles
from ..casc import detect_warcraft_install, get_shared_casc
from ..debuglog import configure_logging
from ..doodad_native import load_doodad_catalog
from ..editor_strings import EditorStringCatalog, load_editor_strings
from ..native_data import NativeDataCatalog
from ..unit_native import load_unit_profiles, load_unit_ui

LOGGER, _SESSION_LOG = configure_logging()


@dataclass(frozen=True)
class NativeCatalogBundle:
    units: NativeDataCatalog
    abilities: dict[str, dict[str, str]]
    doodads: dict[str, dict[str, object]]
    doodad_metadata: dict[str, dict[str, object]]
    editor_strings: EditorStringCatalog


def load_native_catalogs() -> NativeCatalogBundle:
    """Load offline unit metadata plus live/fallback CASC ability and doodad catalogs."""
    units = NativeDataCatalog.load_default(auto_fetch=False)
    abilities: dict[str, dict[str, str]] = {}
    doodads: dict[str, dict[str, object]] = {}
    doodad_metadata: dict[str, dict[str, object]] = {}
    editor_strings = EditorStringCatalog.empty()

    try:
        install = detect_warcraft_install()
        if install:
            casc = get_shared_casc(install)

            # Live Reforged data overrides cached cells, while classic UnitUI-only
            # fields remain as inherited fallbacks for the Object Editor form.
            try:
                live_ui = load_unit_ui(casc)
                unit_ui = units.tables.setdefault("UnitUI", {})
                live_cells = 0
                for raw, row in live_ui.items():
                    target = unit_ui.setdefault(raw, {})
                    target.update(row)
                    live_cells += len(row)
                live_profiles = load_unit_profiles(casc)
                for raw, row in live_profiles.items():
                    units.profiles.setdefault(raw, {}).update(row)
                LOGGER.info(
                    "Native unit CASC merge: unitui_rows=%d cells=%d profiles=%d",
                    len(live_ui), live_cells, len(live_profiles),
                )
            except Exception:
                LOGGER.warning("Native UnitUI/UnitSkin CASC merge unavailable", exc_info=True)

            editor_strings = load_editor_strings(casc)
            abilities = {key: dict(value) for key, value in load_profiles(casc).items()}
            try:
                doodads, doodad_metadata = load_doodad_catalog(casc)
            except Exception:
                LOGGER.warning("Native doodad catalog/metadata unavailable", exc_info=True)
            object_count=sum(1 for row in abilities.values() if row.get("__ability_object__") == "1")
            LOGGER.info(
                "Native ability catalog from CASC/fallback: %d abilities + %d auxiliary Buff/Effect profiles",
                object_count or len(abilities), max(0, len(abilities)-object_count) if object_count else 0,
            )
    except Exception:
        LOGGER.warning(
            "Native CASC catalogs unavailable; standard objects will be limited to cached unit data/map overrides",
            exc_info=True,
        )

    return NativeCatalogBundle(
        units=units,
        abilities=abilities,
        doodads=doodads,
        doodad_metadata=doodad_metadata,
        editor_strings=editor_strings,
    )
