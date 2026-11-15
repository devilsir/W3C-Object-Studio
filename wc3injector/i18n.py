from __future__ import annotations

import re
from dataclasses import replace
from .field_help import describe_field as describe_field_pt

LANGUAGES = ("pt", "en")

UI = {
    "pt": {
        "open_map": "ABRIR MAPA", "save": "SALVAR", "save_as": "SALVAR COMO…",
        "new_object": "+ NOVO OBJETO", "edit_base": "EDITAR BASE POR RAWCODE", "open_log": "ABRIR LOG", "credits": "CRÉDITOS",
        "no_map": "Nenhum mapa aberto", "object": "Objeto", "base": "Base", "table": "Tabela",
        "duplicate": "Duplicar", "delete_override": "Excluir override", "select_object": "Selecione um objeto",
        "spell_level": "Nível da spell", "apply_object": "APLICAR NO OBJETO", "raw_fields": "Raw Fields",
        "field": "Field", "type": "Tipo", "level": "Lvl", "column": "Col", "value": "Valor",
        "add_raw": "+ Raw field", "edit": "Editar", "remove": "Remover", "viewer": "VISUALIZADOR",
        "open_file": "Abrir arquivo…", "open_external": "Abrir externo", "icons_unit": "ÍCONES DO HERÓI / UNIDADE",
        "icons_ability": "ÍCONES DA HABILIDADE", "icon_object": "ÍCONE DO OBJETO", "normal_btn": "Normal BTN",
        "disabled": "Disabled", "passive": "Passive", "ability_test": "TESTE DA HABILIDADE",
        "test_hero": "Herói de teste", "model_spell": "MODELO 3D / PREVIEW", "unit_abilities": "HABILIDADES DA UNIDADE",
        "select_unit_abilities": "Selecione uma unidade para listar as spells e seus ícones.",
        "ability_test_help": "O viewer usa o herói que possui a spell, tenta a animação de cast e permite ligar Caster/Target/Special/Effect/Area/Missile Art.",
        "scope_all": "Todos", "scope_map": "Mapa", "scope_warcraft": "Warcraft",
        "kind_unit": "Unidades / Heróis", "kind_placed_hero": "Heróis no mapa", "kind_ability": "Spells / Habilidades", "kind_doodad": "Doodads",
        "import_model": "Importar MDX/MDL…", "preview": "Visualizar", "warcraft": "Warcraft",
        "custom": "custom", "override": "override", "source": "Origem",
        "lang_pt": "Português", "lang_en": "English",
    },
    "en": {
        "open_map": "OPEN MAP", "save": "SAVE", "save_as": "SAVE AS…",
        "new_object": "+ NEW OBJECT", "edit_base": "EDIT BASE BY RAWCODE", "open_log": "OPEN LOG", "credits": "CREDITS",
        "no_map": "No map open", "object": "Object", "base": "Base", "table": "Table",
        "duplicate": "Duplicate", "delete_override": "Delete override", "select_object": "Select an object",
        "spell_level": "Spell level", "apply_object": "APPLY TO OBJECT", "raw_fields": "Raw Fields",
        "field": "Field", "type": "Type", "level": "Lvl", "column": "Col", "value": "Value",
        "add_raw": "+ Raw field", "edit": "Edit", "remove": "Remove", "viewer": "VIEWER",
        "open_file": "Open file…", "open_external": "Open external", "icons_unit": "HERO / UNIT ICONS",
        "icons_ability": "ABILITY ICONS", "icon_object": "OBJECT ICON", "normal_btn": "Normal BTN",
        "disabled": "Disabled", "passive": "Passive", "ability_test": "ABILITY TEST",
        "test_hero": "Test hero", "model_spell": "3D MODEL / PREVIEW", "unit_abilities": "UNIT ABILITIES",
        "select_unit_abilities": "Select a unit to list its abilities and icons.",
        "ability_test_help": "The viewer uses the hero that owns the ability, tries a cast animation, and can display Caster/Target/Special/Effect/Area/Missile Art.",
        "scope_all": "All", "scope_map": "Map", "scope_warcraft": "Warcraft",
        "kind_unit": "Units / Heroes", "kind_placed_hero": "Heroes on Map", "kind_ability": "Spells / Abilities", "kind_doodad": "Doodads",
        "import_model": "Import MDX/MDL…", "preview": "Preview", "warcraft": "Warcraft",
        "custom": "custom", "override": "override", "source": "Source",
        "lang_pt": "Portuguese", "lang_en": "English",
    },
}

CATEGORY_PT_TO_EN = {
    "Geral": "General", "Habilidades": "Abilities", "Arte": "Art", "Stats": "Stats",
    "Movimento": "Movement", "Combate": "Combat", "Herói": "Hero", "Texto": "Text",
    "Gameplay": "Gameplay", "Raw Fields": "Raw Fields", "Som": "Sound", "Tecnologia": "Techtree",
    "Editor": "Editor", "Pathing": "Pathing",
}
CATEGORY_EN_TO_PT = {v: k for k, v in CATEGORY_PT_TO_EN.items()}

FIELD_LABELS = {
    "pt": {
        "unam":"Nome", "unsf":"Sufixo do editor", "uabi":"Habilidades - Normais", "uhab":"Habilidades - Herói",
        "uabs":"Habilidades - Skin normal", "uhas":"Habilidades - Skin de herói", "utyp":"Classificações",
        "umdl":"Arte - Arquivo do modelo", "usca":"Arte - Escala do modelo", "ussc":"Arte - Escala do círculo de seleção",
        "uico":"Arte - Ícone da interface", "ussi":"Arte - Ícone da tela de pontuação",
        "uclr":"Arte - Cor - Vermelho", "uclg":"Arte - Cor - Verde", "uclb":"Arte - Cor - Azul",
        "uhpm":"Stats - HP máximo", "umpm":"Stats - Mana máxima", "umpi":"Stats - Mana inicial",
        "uhpr":"Stats - Regeneração de HP", "umpr":"Stats - Regeneração de mana", "usid":"Stats - Visão diurna",
        "usin":"Stats - Visão noturna", "ufoo":"Stats - Custo de comida", "ufma":"Stats - Comida produzida",
        "ugol":"Stats - Custo de ouro", "ulum":"Stats - Custo de madeira", "ubld":"Stats - Tempo de construção",
        "umvs":"Movimento - Velocidade base", "umvt":"Movimento - Tipo", "umvh":"Movimento - Altura",
        "umvf":"Movimento - Altura mínima", "umvr":"Movimento - Velocidade de giro", "ucol":"Movimento - Colisão",
        "uacq":"Combate - Alcance de aquisição", "udef":"Combate - Defesa base", "udty":"Combate - Tipo de defesa",
        "ua1b":"Combate - Ataque 1 - Dano base", "ua1d":"Combate - Ataque 1 - Número de dados",
        "ua1s":"Combate - Ataque 1 - Lados por dado", "ua1c":"Combate - Ataque 1 - Intervalo",
        "ua1r":"Combate - Ataque 1 - Alcance", "ua1m":"Combate - Ataque 1 - Projétil",
        "ua1t":"Combate - Ataque 1 - Tipo de ataque", "ua1w":"Combate - Ataque 1 - Tipo de arma",
        "ua1g":"Combate - Ataque 1 - Alvos permitidos", "upro":"Herói - Nomes próprios",
        "upra":"Herói - Atributo primário", "ulev":"Stats - Nível do tipo da unidade", "ustr":"Herói - Força inicial",
        "ustp":"Herói - Força por nível", "uagi":"Herói - Agilidade inicial", "uagp":"Herói - Agilidade por nível",
        "uint":"Herói - Inteligência inicial", "uinp":"Herói - Inteligência por nível",
        "anam":"Nome", "ansf":"Sufixo do editor", "aart":"Arte - Ícone", "alev":"Número de níveis",
        "arlv":"Nível requerido", "alsk":"Intervalo de nível requerido", "aher":"Habilidade de herói",
        "aite":"Habilidade de item", "arac":"Raça", "abpx":"Posição do botão X", "abpy":"Posição do botão Y",
        "ahky":"Atalho", "atp1":"Tooltip", "aub1":"Tooltip estendido", "aret":"Tooltip de pesquisa",
        "arut":"Tooltip estendido de pesquisa", "arhk":"Atalho de pesquisa", "acdn":"Cooldown",
        "amcs":"Custo de mana", "aran":"Alcance de conjuração", "aare":"Área de efeito", "adur":"Duração - normal",
        "ahdu":"Duração - herói", "atar":"Alvos permitidos", "aord":"String de ordem",
        "dnam":"Nome", "dmod":"Modelo", "dmis":"Escala mínima", "dmas":"Escala máxima", "dvar":"Variações", "dsel":"Selecionável",
    },
    "en": {
        "unam":"Name", "unsf":"Editor Suffix", "uabi":"Abilities - Normal", "uhab":"Abilities - Hero",
        "uabs":"Abilities - Normal Skin", "uhas":"Abilities - Hero Skin", "utyp":"Classifications",
        "umdl":"Art - Model File", "usca":"Art - Scaling Value", "ussc":"Art - Selection Circle - Scale",
        "uico":"Art - Icon - Game Interface", "ussi":"Art - Icon - Score Screen",
        "uclr":"Art - Tinting Color - Red", "uclg":"Art - Tinting Color - Green", "uclb":"Art - Tinting Color - Blue",
        "uhpm":"Stats - Hit Points Maximum", "umpm":"Stats - Mana Maximum", "umpi":"Stats - Mana Initial Amount",
        "uhpr":"Stats - Hit Point Regeneration", "umpr":"Stats - Mana Regeneration", "usid":"Stats - Sight Radius (Day)",
        "usin":"Stats - Sight Radius (Night)", "ufoo":"Stats - Food Cost", "ufma":"Stats - Food Produced",
        "ugol":"Stats - Gold Cost", "ulum":"Stats - Lumber Cost", "ubld":"Stats - Build Time",
        "umvs":"Movement - Speed Base", "umvt":"Movement - Type", "umvh":"Movement - Height",
        "umvf":"Movement - Height Minimum", "umvr":"Movement - Turn Rate", "ucol":"Pathing - Collision Size",
        "uacq":"Combat - Acquisition Range", "udef":"Combat - Defense Base", "udty":"Combat - Defense Type",
        "ua1b":"Combat - Attack 1 - Damage Base", "ua1d":"Combat - Attack 1 - Damage Number of Dice",
        "ua1s":"Combat - Attack 1 - Damage Sides per Die", "ua1c":"Combat - Attack 1 - Cooldown Time",
        "ua1r":"Combat - Attack 1 - Range", "ua1m":"Combat - Attack 1 - Projectile Art",
        "ua1t":"Combat - Attack 1 - Attack Type", "ua1w":"Combat - Attack 1 - Weapon Type",
        "ua1g":"Combat - Attack 1 - Targets Allowed", "upro":"Hero - Proper Names",
        "upra":"Hero - Primary Attribute", "ulev":"Stats - Level (unit type)", "ustr":"Hero - Starting Strength",
        "ustp":"Hero - Strength per Level", "uagi":"Hero - Starting Agility", "uagp":"Hero - Agility per Level",
        "uint":"Hero - Starting Intelligence", "uinp":"Hero - Intelligence per Level",
        "anam":"Name", "ansf":"Editor Suffix", "aart":"Art - Icon", "alev":"Number of Levels",
        "arlv":"Required Level", "alsk":"Level Skip Requirement", "aher":"Hero Ability", "aite":"Item Ability",
        "arac":"Race", "abpx":"Button Position X", "abpy":"Button Position Y", "ahky":"Hotkey", "atp1":"Tooltip",
        "aub1":"Ubertip", "aret":"Research Tooltip", "arut":"Research Ubertip", "arhk":"Research Hotkey",
        "acdn":"Cooldown", "amcs":"Mana Cost", "aran":"Cast Range", "aare":"Area of Effect",
        "adur":"Duration - Normal", "ahdu":"Duration - Hero", "atar":"Targets Allowed", "aord":"Order String",
        "dnam":"Name", "dmod":"Model", "dmis":"Minimum Scale", "dmas":"Maximum Scale", "dvar":"Variations", "dsel":"Selectable",
    },
}

EN_HELP = {
    "unam":"Sets the name shown for this unit/hero in selection, tooltips, and other game UI.",
    "unsf":"Adds an editor-only suffix used to organize similar objects. It normally is not shown to players in-game.",
    "upro":"Lists the proper names a hero may receive when created. Names are comma-separated.",
    "upru":"Controls how many entries from Proper Names are eligible for random selection. Example: with 10 names and a value of 3, only the first 3 are used.",
    "uabi":"Lists the unit's normal abilities. Changing it adds/removes active buttons, passives, and ability behavior.",
    "uhab":"Lists hero abilities available to learn through the hero skill system.",
    "umdl":"Selects the MDX/MDL file used to render the unit in-game.",
    "uico":"Selects the unit/hero icon used by the game interface.",
    "usca":"Multiplies the visual size of the 3D model. 1.00 is original size; 2.00 doubles it. It does not change collision or range by itself.",
    "ussc":"Changes the size of the ground selection circle. It does not automatically change pathing/collision.",
    "uhpm":"Sets the unit's maximum hit points.", "umpm":"Sets maximum mana.", "umpi":"Sets starting mana.",
    "uhpr":"Sets hit point regeneration rate.", "umpr":"Sets mana regeneration rate.",
    "usid":"Sets daytime sight radius.", "usin":"Sets nighttime sight radius.",
    "uacq":"Sets how far the unit automatically searches for enemies to acquire as attack targets. This is not necessarily the same as attack range.",
    "ufoo":"Sets food/supply consumed by this unit.", "ufma":"Sets food/supply provided by this unit.",
    "ugol":"Sets the gold cost to train/buy the unit.", "ulum":"Sets the lumber cost to train/buy the unit.",
    "ubld":"Sets training/build time.", "umvs":"Sets maximum movement speed.",
    "umvt":"Sets movement type (foot, fly, float, etc.), affecting where the unit can move and how pathing is handled.",
    "umvh":"Sets the unit movement/flying height.", "umvr":"Sets how quickly the unit can turn.",
    "ucol":"Sets collision/pathing radius. Higher values make the unit occupy more space.",
    "udef":"Sets base armor before upgrades, items, buffs, and bonuses.", "udty":"Sets defense type used by Warcraft damage multipliers.",
    "ua1b":"Sets Attack 1 base damage before dice and other bonuses.", "ua1d":"Sets how many damage dice Attack 1 rolls.",
    "ua1s":"Sets the number of sides on each Attack 1 damage die.", "ua1c":"Sets seconds between Attack 1 attacks. Lower means faster attacks.",
    "ua1r":"Sets maximum Attack 1 range.", "ua1m":"Selects the projectile model used by Attack 1.",
    "ua1t":"Sets Attack 1 attack type, which interacts with defense-type damage multipliers.",
    "ua1w":"Sets Attack 1 weapon behavior such as melee, missile, artillery, etc.",
    "ua1g":"Defines which target categories Attack 1 can hit.",
    "upra":"Selects the hero's primary attribute. It affects base hero damage and which stat is considered primary.",
    "ustr":"Sets starting Strength.", "uagi":"Sets starting Agility.", "uint":"Sets starting Intelligence.",
    "ustp":"Sets Strength gained per level.", "uagp":"Sets Agility gained per level.", "uinp":"Sets Intelligence gained per level.",
    "anam":"Sets the ability name shown in-game and in the editor.", "aart":"Selects the ability icon used in the command card.",
    "alev":"Sets how many levels the ability has and therefore how many level-specific data rows can exist.",
    "arlv":"Sets the minimum hero level required to learn the first level of this ability.",
    "alsk":"Sets how many hero levels must pass between consecutive ability levels.",
    "aher":"Controls whether the ability is treated as a hero ability.", "aite":"Controls whether the ability is treated as an item ability.",
    "acdn":"Sets ability cooldown for this level.", "amcs":"Sets mana cost for this level.", "aran":"Sets cast range for this level.",
    "aare":"Sets area-of-effect radius for this level; exact behavior depends on the base ability.",
    "adur":"Sets duration against normal units.", "ahdu":"Sets duration against heroes.",
    "atar":"Defines which target types the ability can affect.", "aord":"Sets the engine order string used to issue this ability's command.",
    "dmod":"Selects the MDX/MDL model used to render the doodad.", "dmis":"Sets the minimum random scale for placed doodads.",
    "dmas":"Sets the maximum random scale for placed doodads.", "dvar":"Sets the number of model variations available.",
}

PT_TO_EN_WORDS = [
    ("Habilidades", "Abilities"), ("Arte", "Art"), ("Combate", "Combat"), ("Movimento", "Movement"),
    ("Herói", "Hero"), ("Geral", "General"), ("Texto", "Text"), ("Nome", "Name"), ("Nível", "Level"),
    ("Escala", "Scale"), ("Ícone", "Icon"), ("Modelo", "Model"), ("Velocidade", "Speed"), ("Altura", "Height"),
    ("Tipo", "Type"), ("Dano", "Damage"), ("Alcance", "Range"), ("Custo", "Cost"), ("Máximo", "Maximum"),
]
EN_TO_PT_WORDS = [
    ("Abilities", "Habilidades"), ("Art", "Arte"), ("Combat", "Combate"), ("Movement", "Movimento"),
    ("Hero", "Herói"), ("General", "Geral"), ("Text", "Texto"), ("Name", "Nome"), ("Level", "Nível"),
    ("Scale", "Escala"), ("Icon", "Ícone"), ("Model", "Modelo"), ("Speed", "Velocidade"), ("Height", "Altura"),
    ("Type", "Tipo"), ("Damage", "Dano"), ("Range", "Alcance"), ("Cost", "Custo"), ("Maximum", "Máximo"),
]

def tr(lang: str, key: str, default: str | None = None) -> str:
    lang = lang if lang in UI else "pt"
    return UI[lang].get(key, default if default is not None else key)


def translate_category(category: str, lang: str) -> str:
    if lang == "en":
        return CATEGORY_PT_TO_EN.get(category, category)
    return CATEGORY_EN_TO_PT.get(category, category)


def _replace_words(text: str, pairs) -> str:
    out = text
    for a, b in pairs:
        out = re.sub(rf"\b{re.escape(a)}\b", b, out, flags=re.I)
    return out


def translate_field_label(field_id: str, label: str, lang: str) -> str:
    hit = FIELD_LABELS.get(lang, {}).get(field_id)
    if hit:
        return hit
    if lang == "en":
        return _replace_words(label, PT_TO_EN_WORDS)
    return _replace_words(label, EN_TO_PT_WORDS)


def localize_field_def(fd, lang: str):
    return replace(
        fd,
        label=translate_field_label(getattr(fd, "id", ""), getattr(fd, "label", ""), lang),
        category=translate_category(getattr(fd, "category", ""), lang),
    )


def _generic_en(fd) -> str:
    label = getattr(fd, "label", "") or getattr(fd, "id", "")
    category = translate_category(getattr(fd, "category", ""), "en")
    low = label.casefold()
    if "animation" in low:
        return "Controls a model animation parameter, changing how or when an animation is played in-game."
    if "attack" in low:
        return "Controls part of this unit's attack behavior, such as damage, timing, range, projectile, targets, or weapon behavior."
    if "model" in low:
        return "Controls which 3D model or model-related option is used for this object in-game."
    if "icon" in low:
        return "Controls which icon is displayed for this object in the Warcraft III interface."
    if "range" in low:
        return "Controls the relevant maximum distance for this field, such as attack, acquisition, or casting range."
    if "cooldown" in low:
        return "Controls how long this action must wait before it can happen again. Lower values mean it can happen more frequently."
    if "target" in low:
        return "Controls which targets can be selected or affected by this object/action."
    if category == "Art":
        return "Controls a visual property of this object, such as model, animation, icon, scale, color, or attachment behavior."
    if category == "Combat":
        return "Controls a combat property of this object and can change how it attacks, deals damage, or interacts with targets."
    if category == "Movement":
        return "Controls movement or pathing behavior for this object on the map."
    if category == "Abilities":
        return "Controls abilities or ability-related behavior attached to this unit/hero."
    if category == "Stats":
        return "Controls a gameplay statistic such as life, mana, cost, sight, level, or another numeric property."
    return f"Controls the Warcraft III property “{label}” for this object. Changing it affects the corresponding gameplay or visual behavior."


def describe_field(fd, lang: str) -> str:
    if lang != "en":
        return describe_field_pt(fd)
    fid = getattr(fd, "id", "") or ""
    gameplay = EN_HELP.get(fid) or _generic_en(fd)
    technical = (getattr(fd, "help", "") or "").strip()
    native_table = (getattr(fd, "native_table", "") or "").strip()
    native_column = (getattr(fd, "native_column", "") or "").strip()
    details=[]
    if technical:
        details.append(technical)
    elif native_table or native_column:
        details.append(f"{fid} · {native_table}.{native_column}".strip(" ."))
    elif fid:
        details.append(f"Field ID: {fid}")
    return gameplay + (("\n\nTechnical: " + " · ".join(details)) if details else "")
