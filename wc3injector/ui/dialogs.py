from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from ..objectmod import Modification
from ..fields import TYPE_NAMES
from .editor_values import TYPE_MAP, coerce, value_text, DEFAULT_NEW

def _msg(parent, pt: str, en: str) -> str:
    return en if getattr(parent, "language", "pt") == "en" else pt

def _error_text(parent, exc: BaseException) -> str:
    fn = getattr(parent, "_localize_error_text", None)
    return fn(exc) if callable(fn) else str(exc)

class RawDialog(tk.Toplevel):
    def __init__(self, parent, mod: Modification | None, extended: bool):
        super().__init__(parent)
        self.title("Raw field")
        self.resizable(False, False)
        self.result = None
        self.transient(parent)
        self.grab_set()
        self.fid = tk.StringVar(value=mod.field_id if mod else "")
        self.typ = tk.StringVar(value=TYPE_NAMES.get(mod.value_type, "string") if mod else "real")
        self.val = tk.StringVar(value=value_text(mod.value) if mod else "")
        self.level = tk.StringVar(value=str(mod.level if mod else 0))
        self.column = tk.StringVar(value=str(mod.column if mod else 0))
        box = ttk.Frame(self, padding=14); box.pack(fill="both", expand=True)
        fields = [("Field ID", self.fid), (_msg(parent,"Tipo","Type"), self.typ), (_msg(parent,"Valor","Value"), self.val), ("Level", self.level), ("Column", self.column)]
        for i,(label,var) in enumerate(fields):
            ttk.Label(box,text=label).grid(row=i,column=0,sticky="w",pady=4)
            if label in {"Tipo", "Type"}:
                ttk.Combobox(box,textvariable=var,values=list(TYPE_MAP),state="readonly",width=22).grid(row=i,column=1,padx=8,pady=4)
            else:
                e=ttk.Entry(box,textvariable=var,width=28); e.grid(row=i,column=1,padx=8,pady=4)
                if not extended and label in {"Level","Column"}: e.state(["disabled"])
        buttons=ttk.Frame(box); buttons.grid(row=6,column=0,columnspan=2,sticky="e",pady=(12,0))
        ttk.Button(buttons,text=_msg(parent,"Cancelar","Cancel"),command=self.destroy).pack(side="right")
        ttk.Button(buttons,text="OK",command=self.accept).pack(side="right",padx=8)
        self.bind("<Return>",lambda e:self.accept())
        self.wait_window(self)

    def accept(self):
        try:
            fid=self.fid.get().strip()
            if len(fid)!=4: raise ValueError(_msg(self.master,"Field ID precisa ter 4 caracteres","Field ID must be 4 characters"))
            typ=TYPE_MAP[self.typ.get()]
            self.result=Modification(fid,typ,coerce(typ,self.val.get()),int(self.level.get()) if self.level.get() else 0,int(self.column.get()) if self.column.get() else 0)
            self.destroy()
        except Exception as exc:
            messagebox.showerror(_msg(self.master,"Campo inválido","Invalid field"),_error_text(self.master,exc),parent=self)


class NewObjectDialog(tk.Toplevel):
    def __init__(self,parent,kind_hint="unit"):
        super().__init__(parent)
        self.title(_msg(parent,"Novo objeto custom","New custom object"))
        self.result=None; self.transient(parent); self.grab_set(); self.resizable(False,False)
        self.mode=tk.StringVar(value=kind_hint)
        self.base=tk.StringVar(); self.raw=tk.StringVar(); self.name=tk.StringVar()
        box=ttk.Frame(self,padding=16); box.pack(fill="both",expand=True)
        ttk.Label(box,text=_msg(parent,"Tipo","Type")).grid(row=0,column=0,sticky="w",pady=4)
        cb=ttk.Combobox(box,textvariable=self.mode,values=["unit","hero","ability","doodad"],state="readonly",width=24)
        cb.grid(row=0,column=1,padx=8,pady=4); cb.bind("<<ComboboxSelected>>",self.defaults)
        for row,(label,var) in enumerate([("Base rawcode",self.base),(_msg(parent,"Novo rawcode","New rawcode"),self.raw),(_msg(parent,"Nome","Name"),self.name)],1):
            ttk.Label(box,text=label).grid(row=row,column=0,sticky="w",pady=4); ttk.Entry(box,textvariable=var,width=28).grid(row=row,column=1,padx=8,pady=4)
        actions=ttk.Frame(box); actions.grid(row=5,column=0,columnspan=2,sticky="e",pady=(12,0))
        ttk.Button(actions,text=_msg(parent,"Cancelar","Cancel"),command=self.destroy).pack(side="right")
        ttk.Button(actions,text=_msg(parent,"Criar","Create"),command=self.accept).pack(side="right",padx=8)
        self.defaults(); self.wait_window(self)

    def defaults(self,_=None):
        b,r,n=DEFAULT_NEW[self.mode.get()]; self.base.set(b); self.raw.set(r); self.name.set(n)

    def accept(self):
        try:
            if len(self.base.get())!=4 or len(self.raw.get())!=4: raise ValueError(_msg(self.master,"Rawcodes precisam ter 4 caracteres","Rawcodes must be 4 characters"))
            kind="unit" if self.mode.get() in {"unit","hero"} else self.mode.get()
            self.result=(kind,self.base.get(),self.raw.get(),self.name.get())
            self.destroy()
        except Exception as exc: messagebox.showerror(_msg(self.master,"Objeto inválido","Invalid object"),_error_text(self.master,exc),parent=self)
