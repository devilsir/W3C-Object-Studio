from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from ..casc import detect_warcraft_install, get_shared_casc
from ..debuglog import configure_logging
from ..item_catalog import ItemInfo, load_item_catalog
from ..storm import StormArchive

LOGGER, _SESSION_LOG = configure_logging()


class ItemBrowserDialog(tk.Toplevel):
    """World-Editor-like visual item chooser for native and map custom items."""

    def __init__(self, owner, *, initial_raw: str = "", title: str | None = None):
        super().__init__(owner)
        self.owner=owner
        self.session=getattr(owner,"session",None)
        self.result: ItemInfo | None=None
        self.initial_raw=str(initial_raw or "").strip()
        self.items: list[ItemInfo]=[]
        self.filtered: list[ItemInfo]=[]
        self.by_raw: dict[str,ItemInfo]={}
        self._photos: dict[str,object]={}
        self._detail_photo=None
        self._icon_serial=0
        self._loading=False
        self.title(title or self._msg("Escolher item","Choose Item"))
        self.geometry("980x650")
        self.minsize(820,520)
        try:self.transient(owner)
        except Exception:pass
        self.protocol("WM_DELETE_WINDOW",self._cancel)

        self.search_var=tk.StringVar(value="")
        self.source_var=tk.StringVar(value=self._msg("Todos","All"))
        self.level_var=tk.StringVar(value=self._msg("Qualquer nível","Any Level"))
        self.class_var=tk.StringVar(value=self._msg("Qualquer classe","Any Class"))
        self.equip_var=tk.StringVar(value=self._msg("Qualquer equipamento","Any Equipment"))
        self.status_var=tk.StringVar(value=self._msg("Carregando itens do Warcraft e do mapa…","Loading Warcraft and map items…"))
        self.detail_name=tk.StringVar(value="")
        self.detail_meta=tk.StringVar(value="")
        self.detail_icon_path=tk.StringVar(value="")

        self._build()
        self.grab_set()
        self.after(30,self._load_async)

    def _msg(self,pt,en):
        return pt if getattr(self.owner,"language","pt")=="pt" else en

    def _build(self):
        self.columnconfigure(0,weight=1); self.rowconfigure(1,weight=1)
        filters=ttk.Frame(self,padding=(12,10,12,6)); filters.grid(row=0,column=0,sticky="ew")
        filters.columnconfigure(1,weight=1)
        ttk.Label(filters,text=self._msg("Buscar:","Search:")).grid(row=0,column=0,sticky="w")
        search=ttk.Entry(filters,textvariable=self.search_var); search.grid(row=0,column=1,sticky="ew",padx=(6,12))
        search.bind("<KeyRelease>",lambda _e:self._apply_filters())
        self.source_combo=ttk.Combobox(filters,textvariable=self.source_var,state="readonly",width=20); self.source_combo.grid(row=0,column=2,padx=(0,8))
        self.level_combo=ttk.Combobox(filters,textvariable=self.level_var,state="readonly",width=16); self.level_combo.grid(row=0,column=3,padx=(0,8))
        self.class_combo=ttk.Combobox(filters,textvariable=self.class_var,state="readonly",width=20); self.class_combo.grid(row=0,column=4,padx=(0,8))
        self.equip_combo=ttk.Combobox(filters,textvariable=self.equip_var,state="readonly",width=22); self.equip_combo.grid(row=0,column=5)
        for combo in (self.source_combo,self.level_combo,self.class_combo,self.equip_combo):
            combo.bind("<<ComboboxSelected>>",lambda _e:self._apply_filters())

        body=ttk.Panedwindow(self,orient="horizontal"); body.grid(row=1,column=0,sticky="nsew",padx=12,pady=(0,8))
        left=ttk.Frame(body); right=ttk.Frame(body,padding=12); body.add(left,weight=4); body.add(right,weight=2)
        left.columnconfigure(0,weight=1); left.rowconfigure(0,weight=1)
        self.tree=ttk.Treeview(left,columns=("name","raw","level","class","equip","source"),show="tree headings",selectmode="browse")
        self.tree.heading("#0",text=self._msg("Ícone","Icon")); self.tree.column("#0",width=54,stretch=False,anchor="center")
        headings=(("name",self._msg("Nome","Name"),250),("raw","ID",65),("level",self._msg("Nível","Level"),55),("class",self._msg("Classe","Class"),115),("equip",self._msg("Equipamento","Equipment"),125),("source",self._msg("Origem","Source"),135))
        for col,text,width in headings:
            self.tree.heading(col,text=text); self.tree.column(col,width=width,anchor="w",stretch=(col in {"name","source"}))
        ys=ttk.Scrollbar(left,orient="vertical",command=self.tree.yview); self.tree.configure(yscrollcommand=ys.set)
        self.tree.grid(row=0,column=0,sticky="nsew"); ys.grid(row=0,column=1,sticky="ns")
        self.tree.bind("<<TreeviewSelect>>",lambda _e:self._selected_changed())
        self.tree.bind("<Double-1>",lambda _e:self._accept())

        right.columnconfigure(0,weight=1); right.rowconfigure(4,weight=1)
        self.icon_host=tk.Frame(right,width=108,height=108,bg="#11151d",highlightthickness=1,highlightbackground="#343a46")
        self.icon_host.grid(row=0,column=0,sticky="w"); self.icon_host.grid_propagate(False)
        self.icon_label=tk.Label(self.icon_host,text="…",fg="#8f9bad",bg="#11151d",font=("Segoe UI",18,"bold")); self.icon_label.place(relx=.5,rely=.5,anchor="center")
        ttk.Label(right,textvariable=self.detail_name,font=("Segoe UI",12,"bold"),wraplength=310,justify="left").grid(row=1,column=0,sticky="ew",pady=(12,2))
        ttk.Label(right,textvariable=self.detail_meta,style="Source.TLabel",wraplength=310,justify="left").grid(row=2,column=0,sticky="ew")
        ttk.Label(right,textvariable=self.detail_icon_path,style="Source.TLabel",wraplength=310,justify="left").grid(row=3,column=0,sticky="ew",pady=(4,8))
        self.desc=tk.Text(right,height=14,wrap="word",relief="flat",borderwidth=1,state="disabled")
        self.desc.grid(row=4,column=0,sticky="nsew")

        foot=ttk.Frame(self,padding=(12,0,12,12)); foot.grid(row=2,column=0,sticky="ew"); foot.columnconfigure(0,weight=1)
        ttk.Label(foot,textvariable=self.status_var,style="Source.TLabel").grid(row=0,column=0,sticky="w")
        ttk.Button(foot,text=self._msg("Selecionar","Select"),command=self._accept).grid(row=0,column=1,padx=(8,6))
        ttk.Button(foot,text=self._msg("Cancelar","Cancel"),command=self._cancel).grid(row=0,column=2)

    def _load_async(self):
        if self._loading or not self.session:return
        self._loading=True
        def worker():
            try:
                cached=getattr(self.owner,"_placed_item_catalog_cache",None)
                rows=list(cached) if cached is not None else load_item_catalog(self.session)
                if cached is None:
                    try:self.owner._placed_item_catalog_cache=list(rows)
                    except Exception:pass
                error=None
            except Exception as exc: rows=[]; error=exc
            self.after(0,lambda:self._finish_load(rows,error))
        threading.Thread(target=worker,daemon=True,name="wc3-item-catalog").start()

    def _finish_load(self,rows,error):
        self._loading=False
        if error:
            LOGGER.warning("Item chooser load failed",exc_info=error)
            self.status_var.set(self._msg(f"Falha ao carregar itens: {error}",f"Failed to load items: {error}")); return
        self.items=list(rows); self.by_raw={x.rawcode:x for x in rows}
        all_source=self._msg("Todos","All"); native=self._msg("Warcraft","Warcraft"); custom=self._msg("Custom do mapa","Map custom")
        self.source_combo.configure(values=(all_source,native,custom))
        levels=sorted({int(x.level) for x in rows})
        self.level_combo.configure(values=(self._msg("Qualquer nível","Any Level"),)+tuple(str(x) for x in levels))
        classes=sorted({x.item_class for x in rows if x.item_class},key=str.casefold)
        self.class_combo.configure(values=(self._msg("Qualquer classe","Any Class"),)+tuple(classes))
        equips=sorted({x.equipment_type for x in rows if x.equipment_type},key=str.casefold)
        self.equip_combo.configure(values=(self._msg("Qualquer equipamento","Any Equipment"),)+tuple(equips))
        self._apply_filters()
        if self.initial_raw and self.initial_raw in self.by_raw:
            self.after(20,lambda:self._select_raw(self.initial_raw))

    def _apply_filters(self):
        if not self.items:return
        q=self.search_var.get().strip().casefold()
        src=self.source_var.get(); level=self.level_var.get(); cls=self.class_var.get(); equip=self.equip_var.get()
        any_src=self._msg("Todos","All"); native=self._msg("Warcraft","Warcraft"); custom=self._msg("Custom do mapa","Map custom")
        any_level=self._msg("Qualquer nível","Any Level"); any_class=self._msg("Qualquer classe","Any Class"); any_equip=self._msg("Qualquer equipamento","Any Equipment")
        rows=[]
        for item in self.items:
            if q and q not in " ".join((item.name,item.rawcode,item.tooltip,item.ubertip,item.description,item.item_class,item.equipment_type)).casefold():continue
            if src!=any_src:
                if src==custom and not item.custom:continue
                if src==native and item.custom:continue
            if level!=any_level and str(item.level)!=level:continue
            if cls!=any_class and item.item_class!=cls:continue
            if equip!=any_equip and item.equipment_type!=equip:continue
            rows.append(item)
        self.filtered=rows
        self.tree.delete(*self.tree.get_children())
        for idx,item in enumerate(rows):
            iid=f"i{idx}"
            self.tree.insert("","end",iid=iid,text="",values=(item.name,item.rawcode,item.level,item.item_class or "—",item.equipment_type or "—",item.source))
        self.status_var.set(self._msg(f"{len(rows)} itens exibidos · {sum(1 for x in rows if x.custom)} custom do mapa",f"{len(rows)} items shown · {sum(1 for x in rows if x.custom)} map custom"))
        self._load_visible_icons(rows[:100])

    def _select_raw(self,raw):
        for idx,item in enumerate(self.filtered):
            if item.rawcode==raw:
                iid=f"i{idx}"; self.tree.selection_set(iid); self.tree.focus(iid); self.tree.see(iid); self._selected_changed(); return

    def _selected_item(self):
        sel=self.tree.selection()
        if not sel:return None
        try:idx=int(sel[0][1:])
        except Exception:return None
        return self.filtered[idx] if 0<=idx<len(self.filtered) else None

    def _selected_changed(self):
        item=self._selected_item()
        if not item:return
        self.detail_name.set(item.name or item.rawcode)
        meta=[item.rawcode]
        if item.level or item.level==0: meta.append(self._msg(f"Nível {item.level}",f"Level {item.level}"))
        if item.item_class:meta.append(item.item_class)
        if item.equipment_type:meta.append(item.equipment_type)
        meta.append(item.source)
        if item.base_id and item.base_id!=item.rawcode:meta.append(self._msg(f"base {item.base_id}",f"base {item.base_id}"))
        self.detail_meta.set(" · ".join(meta)); self.detail_icon_path.set(item.icon or self._msg("Sem caminho de ícone","No icon path"))
        self.desc.configure(state="normal"); self.desc.delete("1.0","end"); self.desc.insert("1.0",item.detail_text or self._msg("Sem descrição disponível.","No description available.")); self.desc.configure(state="disabled")
        self._load_one_icon(item,detail=True)

    def _resolve_icon_blob(self,item:ItemInfo):
        ref=str(item.icon or "").strip()
        if not ref or not self.session:return None,None
        try:
            with StormArchive(self.session.map_path,read_only=True) as arc:
                resolver=getattr(self.owner,"_resolve_archive_path",None)
                actual=resolver(arc,ref) if callable(resolver) else (ref if arc.has(ref) else None)
                if actual:return arc.read(actual),actual
        except Exception:LOGGER.debug("Map item icon lookup failed %s",ref,exc_info=True)
        try:
            install=detect_warcraft_install()
            if install:
                casc=get_shared_casc(install); actual,data=casc.read_with_path(ref,preference="hd"); return data,actual
        except Exception:LOGGER.debug("CASC item icon lookup failed %s",ref,exc_info=True)
        return None,None

    def _decode(self,data,size):
        decoder=getattr(self.owner,"_decode_icon_photo",None)
        if callable(decoder):return decoder(data,size)
        from io import BytesIO
        from PIL import Image,ImageTk
        img=Image.open(BytesIO(data)).convert("RGBA"); img.thumbnail((size,size),Image.Resampling.LANCZOS); return ImageTk.PhotoImage(img)

    def _load_visible_icons(self,items):
        self._icon_serial+=1; serial=self._icon_serial
        def worker():
            loaded=[]; casc=None
            try:
                install=detect_warcraft_install()
                if install:
                    try:casc=get_shared_casc(install)
                    except Exception:casc=None
                with StormArchive(self.session.map_path,read_only=True) as arc:
                    resolver=getattr(self.owner,"_resolve_archive_path",None)
                    for item in items:
                        if serial!=self._icon_serial:return
                        ref=str(item.icon or "").strip()
                        if not ref:continue
                        data=None
                        try:
                            actual=resolver(arc,ref) if callable(resolver) else (ref if arc.has(ref) else None)
                            if actual:data=arc.read(actual)
                        except Exception:
                            data=None
                        if data is None and casc is not None:
                            try:_actual,data=casc.read_with_path(ref,preference="hd")
                            except Exception:data=None
                        if data:loaded.append((item.rawcode,data))
            except Exception:
                LOGGER.debug("Bulk item icon load failed",exc_info=True)
            def show():
                if serial!=self._icon_serial:return
                for raw,data in loaded:
                    try:self._photos[raw]=self._decode(data,36)
                    except Exception:continue
                for idx,item in enumerate(self.filtered):
                    photo=self._photos.get(item.rawcode)
                    if photo:
                        try:self.tree.item(f"i{idx}",image=photo)
                        except Exception:pass
            self.after(0,show)
        threading.Thread(target=worker,daemon=True,name="wc3-item-icons").start()

    def _load_one_icon(self,item,detail=False):
        # Detail preview deliberately decodes a larger copy instead of reusing
        # the 36 px Treeview thumbnail.
        self.icon_label.configure(image="",text="…")
        def worker():
            data,actual=self._resolve_icon_blob(item)
            def show():
                if self._selected_item() is not item:return
                if not data:self.icon_label.configure(image="",text="—"); return
                try:
                    photo=self._decode(data,96); self._detail_photo=photo; self.icon_label.configure(image=photo,text=""); self.icon_label.image=photo
                except Exception:self.icon_label.configure(image="",text="!")
            self.after(0,show)
        threading.Thread(target=worker,daemon=True,name="wc3-item-detail-icon").start()

    def _accept(self):
        item=self._selected_item()
        if item is None:
            messagebox.showinfo(self._msg("Item","Item"),self._msg("Selecione um item.","Select an item."),parent=self);return
        self.result=item
        try:self.grab_release()
        except Exception:pass
        self.destroy()

    def _cancel(self):
        self.result=None
        try:self.grab_release()
        except Exception:pass
        self.destroy()


def choose_item(owner, *, initial_raw: str = "", title: str | None = None) -> ItemInfo | None:
    dialog=ItemBrowserDialog(owner,initial_raw=initial_raw,title=title)
    owner.wait_window(dialog)
    return dialog.result
