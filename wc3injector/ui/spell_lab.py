from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from ..debuglog import configure_logging, latest_log_path, log_exception
from ..editor import EditSession
from ..fields import FIELD_SETS, FIELD_BY_ID, TYPE_NAMES, NAME_TYPES
from ..injector import auto_assets
from ..modelscan import scan_model
from ..objectmod import Modification, TYPE_INT, TYPE_REAL, TYPE_UNREAL, TYPE_STRING
from ..storm import StormArchive
from ..casc import CascStorage, CascError, detect_warcraft_install, remember_warcraft_install
from ..ability_native import resolve_profile_field, profile_field_value
from ..unit_native import resolve_unit_visual
from ..viewer_server import launch_viewer, prepare_viewer_url
from ..visuals import best_sibling_visual, infer_icon_import, related_icon_variants
from ..webview_profile import prepare_webview2_profile
from ..i18n import tr as i18n_tr, localize_field_def, describe_field as describe_field_i18n, translate_category
from ..paths import ASSETS_DIR
from .editor_values import TYPE_MAP, KIND_LABELS, ABILITY_EFFECT_FIELDS, DEFAULT_NEW, coerce, value_text, field_value_text, coerce_field
from .widgets import ScrollForm, AdaptiveFieldRow
from .dialogs import RawDialog, NewObjectDialog

LOGGER, _SESSION_LOG = configure_logging()


class SpellLabMixin:
    # Keep this mixin independently testable. In the full ObjectStudio MRO,
    # LocalizationMixin._msg takes precedence; standalone probes fall back here.
    def _msg(self, pt: str, en: str) -> str:
            return en if getattr(self, "language", "pt") == "en" else pt

    def _model_reference_for_unit(self, table: str, oid: str, base: str, rec):
            """Resolve a display/test model for any unit record without changing selection."""
            if not self.session:
                return None, ""
            native_fallback=None
            mod,source,_explicit=self.session.resolve_map_value("unit",table,oid,"umdl",level=0,column=0)
            if mod is not None and str(mod.value).strip():
                value=str(mod.value).strip()
                if table=="custom" and source.startswith("base nativa"):
                    native_fallback=(value,source)
                else:
                    return value,source
            if table=="custom":
                match=best_sibling_visual(
                    self.session.list_records("unit"), oid, base, rec.modifications, "umdl", self.record_name("unit",rec)
                )
                if match:
                    return match.value,self._msg(f"visual inferido de {match.object_id} · {match.reason}", f"visual inferred from {match.object_id} · {match.reason}")
            if native_fallback is not None:
                return native_fallback
            return None, source if 'source' in locals() else ""

    def _unit_has_ability(self, table: str, oid: str, ability_id: str) -> tuple[bool, bool, str]:
            """Return (contains, explicit, field-label) for a unit's ability lists."""
            if not self.session:
                return False,False,""
            found=[]; explicit=False
            labels={"uabi":"Normal","uhab":("Herói" if self.language=="pt" else "Hero"),"uabs":"Normal Skin","uhas":"Hero Skin"}
            for fid in ("uabi","uhab","uabs","uhas"):
                try:
                    mod,source,is_explicit=self.session.resolve_map_value("unit",table,oid,fid,level=0,column=0)
                except Exception:
                    continue
                if mod is None:
                    continue
                if ability_id in self._split_rawcodes(str(mod.value)):
                    found.append(labels[fid]); explicit = explicit or bool(is_explicit)
            return bool(found),explicit," + ".join(found)

    def _ability_test_candidates_for_current(self) -> list[dict]:
            """Find map units/heroes that actually carry the selected custom spell."""
            if not self.session or self.current_kind!="ability" or not self.current_id:
                return []
            ability_id=self.current_id
            rows=[]
            for table,oid,base,rec in self.session.list_records("unit"):
                contains,explicit,where=self._unit_has_ability(table,oid,ability_id)
                if not contains:
                    continue
                model_ref,model_source=self._model_reference_for_unit(table,oid,base,rec)
                if not model_ref:
                    continue
                is_hero=bool((oid or base)[:1].isupper())
                score=(220 if explicit else 120)+(90 if is_hero else 0)+(35 if table=="custom" else 0)
                if not model_source.startswith("base nativa"):
                    score+=100
                name=self.record_name("unit",rec)
                rows.append({
                    "table":table,"id":oid,"base":base,"record":rec,"name":name,
                    "model_ref":model_ref,"model_source":model_source,"score":score,"where":where,
                    "label":f"{oid}  {name}",
                })
            rows.sort(key=lambda x:(-x["score"],x["name"].casefold(),x["id"]))
            if rows:
                return rows[:40]
    
            # If no unit references the ability directly, still give the user a test
            # dummy from the map instead of an empty viewer. Prefer custom heroes with
            # imported/sibling visuals because native CASC models are not inside MPQ.
            fallback=[]
            # If spells are attached dynamically by JASS/Lua, uabi/uhab cannot tell us who
            # owns them. Rank fallback heroes from the spell name/icon so a Kaido spell
            # selects a Kaido model instead of the first alphabetical hero in the map.
            ability_icon=self._ability_value("aart",level=0)
            hint_text=(str(self.title_var.get())+" "+ability_icon).casefold()
            stop={"btn","disbtn","opwu","skill","spell","ability","commandbuttons","replaceabletextures","level","dragon","the","of"}
            hint_tokens=[t for t in re.findall(r"[a-z0-9]+",hint_text) if len(t)>=3 and t not in stop]
            for table,oid,base,rec in self.session.list_records("unit"):
                if not (oid or base)[:1].isupper():
                    continue
                model_ref,model_source=self._model_reference_for_unit(table,oid,base,rec)
                if not model_ref or model_source.startswith("base nativa"):
                    continue
                name=self.record_name("unit",rec)
                icon_hint=""
                try:
                    imod,_,_=self.session.resolve_map_value("unit",table,oid,"uico",level=0,column=0)
                    if imod is not None: icon_hint=str(imod.value or "")
                except Exception:
                    pass
                hay=(name+" "+model_ref+" "+icon_hint+" "+oid).casefold()
                matched=sorted({t for t in hint_tokens if t in hay})
                score=len(matched)*180
                where=(self._msg("inferido por ", "inferred by ") + ", ".join(matched)) if matched else "fallback"
                fallback.append({
                    "table":table,"id":oid,"base":base,"record":rec,"name":name,
                    "model_ref":model_ref,"model_source":model_source,"score":score,"where":where,
                    "label":f"{oid}  {name}  "+self._msg("(teste)", "(test)"),
                })
            fallback.sort(key=lambda x:(-x["score"],x["name"].casefold(),x["id"]))
            return fallback[:40]

    def refresh_ability_test_controls(self):
            if self.current_kind!="ability" or not self.current_id:
                self.ability_test_candidates=[]; self.ability_test_lookup={}
                self.ability_test_hero_combo.configure(values=())
                self.ability_test_hero_var.set("")
                self.ability_test_info_var.set("")
                return
            previous=self.ability_test_hero_var.get()
            rows=self._ability_test_candidates_for_current()
            demo=self._classify_ability_demo()
            kind=demo.get("kind","self")
            if self.language=="pt":
                demo_labels={
                    "aura":"AUTO · Aura: Paladin + aliado",
                    "ally":"AUTO · Aliado: Paladin → Footman",
                    "enemy":"AUTO · Inimigo: Archmage → Grunt",
                    "self":"AUTO · Self/instantânea: Archmage",
                }
                info_labels={
                    "aura":"Cena automática de aura: herói base do Warcraft com um aliado dentro da aura.",
                    "ally":"Cena automática: um herói base do Warcraft usa a habilidade em um aliado.",
                    "enemy":"Cena automática: um herói base do Warcraft usa a habilidade em um inimigo.",
                    "self":"Cena automática: herói base do Warcraft demonstra a habilidade em si/sem alvo.",
                }
            else:
                demo_labels={
                    "aura":"AUTO · Aura: Paladin + ally",
                    "ally":"AUTO · Ally: Paladin → Footman",
                    "enemy":"AUTO · Enemy: Archmage → Grunt",
                    "self":"AUTO · Self/instant: Archmage",
                }
                info_labels={
                    "aura":"Automatic aura scene: a Warcraft base hero with an ally inside the aura.",
                    "ally":"Automatic scene: a Warcraft base hero casts the ability on an ally.",
                    "enemy":"Automatic scene: a Warcraft base hero casts the ability on an enemy.",
                    "self":"Automatic scene: a Warcraft base hero demonstrates the ability on self/no target.",
                }
            auto={
                "table":"native","id":"AUTO","base":"AUTO","record":None,
                "name":demo_labels.get(kind,demo_labels["self"]),"model_ref":"",
                "model_source":self._msg("CASC automático", "automatic CASC"),"score":99999,"where":"auto","auto":True,
                "label":demo_labels.get(kind,demo_labels["self"]),
            }
            all_rows=[auto,*rows]
            self.ability_test_candidates=all_rows
            self.ability_test_lookup={x["label"]:x for x in all_rows}
            values=[x["label"] for x in all_rows]
            self.ability_test_hero_combo.configure(values=values)
            if previous in self.ability_test_lookup:
                chosen=previous
            else:
                chosen=auto["label"]
            self.ability_test_hero_var.set(chosen)
            row=self.ability_test_lookup.get(chosen)
            if row and row.get("auto"):
                self.ability_test_info_var.set(info_labels.get(kind,info_labels["self"]) + self._msg(" Modelos e efeitos vêm do CASC.", " Models and effects come from CASC."))
            elif row:
                direct=row["where"]!="fallback"
                self.ability_test_info_var.set(self._msg(
                    (f"Usado por esta spell ({row['where']}). " if direct else "Nenhum usuário direto encontrado; usando herói de teste. ") + f"Modelo: {row['model_ref']}",
                    (f"Used by this ability ({row['where']}). " if direct else "No direct user found; using a test hero. ") + f"Model: {row['model_ref']}",
                ))
            else:
                self.ability_test_info_var.set(info_labels.get(kind,info_labels["self"]))

    def _selected_ability_test_candidate(self):
            return self.ability_test_lookup.get(self.ability_test_hero_var.get())

    def _ability_value(self, field_id: str, *, level: int | None = None) -> str:
            """Resolve an ability field from map object-data for preview purposes."""
            if not self.session or self.current_kind!="ability" or not self.current_id:
                return ""
            levels=[]
            if level is not None:
                levels.append(level)
            levels.extend([0,1])
            seen=set()
            for lvl in levels:
                if lvl in seen: continue
                seen.add(lvl)
                try:
                    mod,_source,_explicit=self.session.resolve_map_value(
                        "ability",self.current_table,self.current_id,field_id,level=lvl,column=0
                    )
                except Exception:
                    mod=None
                if mod is not None and str(mod.value).strip():
                    return str(mod.value).strip()
            # Fall back to any raw modification because older/custom editors do not
            # always agree whether art fields are stored at level 0 or 1.
            for mod in self.working_mods:
                if mod.field_id==field_id and str(mod.value).strip():
                    return str(mod.value).strip()
            return ""

    @staticmethod
    def _split_model_refs(value: str) -> list[str]:
            out=[]; seen=set()
            for part in (value or "").replace(";",",").split(","):
                ref=part.strip().strip('"')
                if not ref or ref.casefold() in seen:
                    continue
                if not ref.casefold().endswith((".mdx",".mdl")):
                    # Object Editor often omits the extension. Prefer MDX.
                    ref += ".mdx"
                seen.add(ref.casefold()); out.append(ref)
            return out

    @staticmethod
    def _effect_placement_from_text(text: str) -> str:
            low=(text or "").casefold()
            if any(x in low for x in ("missile","projectile","bullet","shot")): return "missile"
            if any(x in low for x in ("target","impact","hit","victim")): return "target"
            if any(x in low for x in ("area","aoe","ground","destination")): return "area"
            # Aura/cyclone/tornado effects in OPWU are commonly centered on the caster.
            return "caster"

    def _ability_import_effect_candidates(self, candidate: dict | None = None) -> list[dict]:
            # Preview-only inference for scripted spells whose art is not stored in w3a.
            if not self.session or self.current_kind!="ability" or not self.current_id:
                return []
            ability_name=str(self.title_var.get() or "")
            icon_ref=self._ability_value("aart",level=0)
            hero_name=str((candidate or {}).get("name", ""))
            stop={"btn","disbtn","pasbtn","opwu","war3mapimported","commandbuttons","replaceabletextures","skill","spell","ability","hero","model","mdx","mdl","blp","tga","dds","level","teste","test"}
            ability_tokens=[x for x in re.findall(r"[a-z0-9]+",(ability_name+" "+Path(icon_ref.replace("\\","/")).stem).casefold()) if len(x)>=3 and x not in stop]
            hero_tokens=[x for x in re.findall(r"[a-z0-9]+",hero_name.casefold()) if len(x)>=3 and x not in stop]
            out=[]
            for entry in self.session.imports.entries:
                path=entry.archive_path
                if not path.casefold().endswith((".mdx",".mdl")):
                    continue
                low=path.replace("\\","/").casefold()
                matched=[t for t in ability_tokens if t in low]
                hmatched=[t for t in hero_tokens if t in low]
                # A bare character name is not enough: it would select the unit model itself.
                rare=[t for t in matched if len(t)>=6 and t not in {"dragon"}]
                if len(set(matched))<2 and not rare:
                    continue
                score=len(set(matched))*4+len(set(hmatched))
                if score<8:
                    continue
                out.append({
                    "field":"inferred",
                    "label":self._msg("Import inferido · ", "Inferred import · ")+Path(low).stem,
                    "archive_ref":path,
                    "placement":self._effect_placement_from_text(low+" "+ability_name),
                    "source":self._msg("import inferido (", "inferred import (")+", ".join(sorted(set(matched))[:5])+")",
                    "score":score,
                })
            out.sort(key=lambda x:(-x["score"],x["archive_ref"].casefold()))
            return out[:8]

    def _script_effect_references_from_arc(self, arc: StormArchive, ability_id: str) -> list[dict]:
            """Find model literals in the local spell function, not a huge JASS window.

            v3.9.4 scanned roughly 20k characters around a rawcode. In a large OPWU
            script that crosses several unrelated spell functions and produced bogus
            previews (Death Pact + Shockwave + Starfall + Blink on the same ability).
            JASS can be bounded reliably by function/endfunction; Lua gets a small
            local window instead of pretending regex can parse nested blocks.
            """
            out=[]; seen=set()
            model_re=re.compile(r"[\"']([^\"'\r\n]+?\.(?:mdx|mdl))[\"']",re.I)

            def jass_scope(text: str, pos: int) -> str:
                before=text.rfind("\nfunction ", max(0,pos-16000), pos+1)
                if before < 0 and text.startswith("function "):
                    before=0
                elif before >= 0:
                    before += 1
                after=text.find("\nendfunction", pos, min(len(text),pos+24000))
                if before >= 0 and after >= 0 and before <= pos <= after:
                    return text[before:after+len("\nendfunction")]
                return text[max(0,pos-1800):min(len(text),pos+2600)]

            for script_name in ("war3map.j","war3map.lua"):
                try:
                    if not arc.has(script_name): continue
                    text=arc.read(script_name).decode("utf-8",errors="replace")
                except Exception:
                    continue
                positions=[m.start() for m in re.finditer(re.escape(ability_id),text,re.I)]
                for pos in positions[:40]:
                    segment=jass_scope(text,pos) if script_name.endswith(".j") else text[max(0,pos-1600):min(len(text),pos+2200)]
                    for m in model_re.finditer(segment):
                        ref=self._norm_archive_path(m.group(1).strip())
                        key=ref.casefold()
                        if key in seen: continue
                        context=segment[max(0,m.start()-220):min(len(segment),m.end()+220)]
                        seen.add(key)
                        out.append({
                            "field":"script",
                            "label":script_name+" · "+Path(ref.replace("\\","/")).stem,
                            "archive_ref":ref,
                            "placement":self._effect_placement_from_text(context+" "+ref),
                            "source":self._msg("referência no mesmo bloco/função de ", "reference in the same block/function as ")+ability_id+self._msg(" em ", " in ")+script_name,
                        })
                        if len(out)>=12:return out
            return out

    def _ability_map_field_state(self, field_id: str, *, level: int | None = None) -> tuple[str, bool]:
            """Return (resolved map value, explicitly overridden-to-empty).
    
            Native/CASC fallback must respect an intentional empty override, otherwise
            a map author cannot suppress Blizzard's inherited art.
            """
            value=self._ability_value(field_id,level=level)
            explicit_empty=False
            for mod in self.working_mods:
                if mod.field_id==field_id and (level is None or mod.level in {0,level}):
                    if not str(mod.value).strip():
                        explicit_empty=True
                    elif str(mod.value).strip():
                        return str(mod.value).strip(),False
            return value,explicit_empty

    def _ability_effect_references(self, native_values: dict[str,str] | None = None) -> list[dict]:
            effects=[]; native_values=native_values or {}
            for fid,label,placement in ABILITY_EFFECT_FIELDS:
                raw,explicit_empty=self._ability_map_field_state(fid,level=self.current_level)
                if not raw and not explicit_empty:
                    raw=str(native_values.get(fid,"") or "")
                for idx,ref in enumerate(self._split_model_refs(raw)):
                    effects.append({
                        "field":fid,"label":label if idx==0 else f"{label} {idx+1}",
                        "archive_ref":ref,"placement":placement,
                        "source":self._msg("mapa", "map") if self._ability_value(fid,level=self.current_level) else self._msg("CASC/nativo", "CASC/native"),
                    })
            return effects

    def _ability_cast_animation_hint(self, native_value: str = "") -> str:
            raw,explicit_empty=self._ability_map_field_state("aani",level=0)
            if raw or explicit_empty:
                return raw
            return native_value or ""

    def _ability_resolved_field(self, field_id: str, *, level: int | None = None) -> str:
            raw,explicit_empty=self._ability_map_field_state(field_id,level=level)
            if raw or explicit_empty:
                return raw
            if not self.session or self.current_kind!="ability" or not self.current_id:
                return ""
            row=self.session.native_abilities.get(str(self.current_base or self.current_id),{})
            lvl=max(1,int(level or self.current_level or 1))
            value,_source=profile_field_value(row,field_id,lvl)
            return str(value or "").strip()

    @staticmethod
    def _generic_demo_unit(role: str) -> dict[str,str]:
            presets={
                "aura": {"rawcode":"Hpal","name":"Paladin"},
                "ally_caster": {"rawcode":"Hpal","name":"Paladin"},
                "enemy_caster": {"rawcode":"Hamg","name":"Archmage"},
                "self_caster": {"rawcode":"Hamg","name":"Archmage"},
                "ally_target": {"rawcode":"hfoo","name":"Footman"},
                "enemy_target": {"rawcode":"ogru","name":"Grunt"},
            }
            return dict(presets.get(role, presets["self_caster"]))

    def _classify_ability_demo(self) -> dict[str,str]:
            targets=self._ability_resolved_field("atar",level=self.current_level).casefold()
            order=self._ability_resolved_field("aord",level=0).casefold()
            tooltip=self._ability_resolved_field("atp1",level=self.current_level).casefold()
            ubertip=self._ability_resolved_field("aub1",level=self.current_level).casefold()
            name=(str(self.title_var.get() or "")+" "+self._ability_resolved_field("aart",level=0)).casefold()
            text=" ".join(x for x in (name,targets,order,tooltip,ubertip) if x).casefold()
            area_text=self._ability_resolved_field("aare",level=self.current_level).strip()
            try:
                area=float(area_text or 0)
            except Exception:
                area=0.0
            effects=self._ability_effect_references({})
            placements={str(x.get("placement","caster")).casefold() for x in effects}
            has_friend=any(x in targets for x in ("friend","ally"))
            has_enemy=("enemy" in targets) or (",enem" in targets) or targets.startswith("enem") or (",enemies" in targets)
            aura_keywords=(" aura","aura ","aura)","brilliance","devotion","trueshot","endurance","unholy aura","thorns aura")
            is_aura=(
                any(k in text for k in aura_keywords)
                or (area>0 and has_friend and not has_enemy and not ({"missile","target"} & placements))
            )
            if is_aura:
                return {"kind":"aura","label":"aura"}
            if has_friend and not has_enemy:
                return {"kind":"ally","label":"ally-target"}
            if has_enemy or ({"missile","target"} & placements):
                return {"kind":"enemy","label":"enemy-target"}
            return {"kind":"self","label":"self"}

    def _ability_preview_setup(self, candidate: dict | None = None) -> dict[str, object]:
            demo=self._classify_ability_demo()
            kind=demo["kind"]
            fallback=not candidate or candidate.get("auto") or candidate.get("where")=="fallback" or self.current_table=="native"
            if fallback or kind=="aura":
                caster_role="ally_caster" if kind in {"aura","ally"} else ("enemy_caster" if kind=="enemy" else "self_caster")
                caster=self._generic_demo_unit(caster_role)
            else:
                caster={
                    "rawcode":str(candidate.get("id") or candidate.get("base") or ""),
                    "name":str(candidate.get("name") or candidate.get("id") or "Caster"),
                    "model_ref":str(candidate.get("model_ref") or ""),
                }
            actors=[]
            if kind=="ally":
                actors.append({**self._generic_demo_unit("ally_target"),"role":"ally"})
            elif kind=="enemy":
                actors.append({**self._generic_demo_unit("enemy_target"),"role":"enemy"})
            elif kind=="aura":
                actors.append({**self._generic_demo_unit("ally_target"),"role":"ally"})
            return {
                "kind":kind,
                "label":demo["label"],
                "caster":caster,
                "actors":actors,
                "target_role":"ally" if kind in {"ally","aura"} else ("enemy" if kind=="enemy" else "self"),
            }
