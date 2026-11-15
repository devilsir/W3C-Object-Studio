from pathlib import Path
import sys

from wc3injector.native_data import (
    ensure_native_data, NativeDataCatalog, REQUIRED_FILES,
    NATIVE_DATASET_VERSION,
)
from wc3injector.ability_native import ensure_ability_fallback_profiles, ABILITY_PROFILE_FILES
from wc3injector.doodad_native import ensure_doodad_fallback_data, DOODAD_DATA_FILE, DOODAD_METADATA_FILE

root=Path(__file__).resolve().parent
folder=root/"native_data"
print("WC3 Object Studio — base nativa do Object Editor")
print("Destino:",folder)
print("Dataset:",NATIVE_DATASET_VERSION)

downloaded,failed=ensure_native_data(folder,timeout=15.0)
if downloaded:
    print("Unit/Hero baixados/atualizados:",", ".join(downloaded))

# CASC is preferred at runtime, but several CascLib/Reforged installs cannot
# open enUS/locale *AbilityStrings entries by name. Keep the official classic
# profile text as an offline fallback so every standard spell still gets a name,
# tooltip and art metadata.
ability_downloaded,ability_failed=ensure_ability_fallback_profiles(folder,timeout=8.0)
if ability_downloaded:
    print("Ability fallback baixado/atualizado:",", ".join(ability_downloaded))

# DoodadMetaData is required to map dfil/dmis/... to Doodads.slk columns. CASC
# remains the primary source; these files make the browser complete if direct
# lookup fails on a particular install.
doodad_downloaded,doodad_failed=ensure_doodad_fallback_data(folder,timeout=8.0)
if doodad_downloaded:
    print("Doodad fallback baixado/atualizado:",", ".join(doodad_downloaded))

missing=[name for name in REQUIRED_FILES if not (folder/name).exists() or (folder/name).stat().st_size <= 100]
if failed:
    print("Avisos Unit/Hero:")
    for item in failed: print(" -",item)
if ability_failed:
    print("Avisos Ability fallback (CASC ainda será tentado):")
    for item in ability_failed: print(" -",item)
if doodad_failed:
    print("Avisos Doodad fallback (CASC ainda será tentado):")
    for item in doodad_failed: print(" -",item)

cat=NativeDataCatalog.load(folder)
print(f"Metadata: {len(cat.metadata)} campos de Unit/Hero")
for name,table in cat.tables.items(): print(f"{name}: {len(table)} objetos nativos")
print(f"Profiles: {len(cat.profiles)} objetos")
ability_local=sum(1 for name in ABILITY_PROFILE_FILES if (folder/name).is_file() and (folder/name).stat().st_size>100)
print(f"Ability profiles offline: {ability_local}/{len(ABILITY_PROFILE_FILES)}")
print(f"Doodad fallback: data={'OK' if (folder/DOODAD_DATA_FILE).exists() else 'CASC'} metadata={'OK' if (folder/DOODAD_METADATA_FILE).exists() else 'CASC'}")

if missing or not cat.ready:
    print("\n[AVISO] Base nativa Unit/Hero incompleta.")
    print("O editor ainda abre, mas campos cujo valor existe apenas no Warcraft podem continuar vazios.")
    if missing: print("Faltando:",", ".join(missing))
    print("Rode este setup novamente com internet ou coloque seus SLKs/TXTs extraídos na pasta native_data.")
    sys.exit(2)
print("\nOK — valores-base + metadata do Object Editor estão disponíveis.")
