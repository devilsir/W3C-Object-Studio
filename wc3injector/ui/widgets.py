from __future__ import annotations

import tkinter as tk
from tkinter import ttk

class ScrollForm(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, padding=12)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))


class HelpBalloon(tk.Toplevel):
    def __init__(self, anchor, *, title: str, message: str):
        super().__init__(anchor)
        self.withdraw()
        self.overrideredirect(True)
        self.transient(anchor.winfo_toplevel())
        try: self.attributes("-topmost", True)
        except Exception: pass
        outer=tk.Frame(self, bg="#8d8d8d", bd=1)
        outer.pack(fill="both", expand=True)
        shell=tk.Frame(outer, bg="#fffbe8", padx=11, pady=9)
        shell.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(shell, text=title, bg="#fffbe8", fg="#111111", font=("Segoe UI", 9, "bold"), anchor="w", justify="left").pack(anchor="w", fill="x")
        tk.Label(shell, text=message, bg="#fffbe8", fg="#202020", font=("Segoe UI", 9), justify="left", anchor="nw", wraplength=420).pack(anchor="w", fill="both", expand=True, pady=(6,0))
        tk.Label(shell, text="ESC, clique fora ou ? para fechar", bg="#fffbe8", fg="#777777", font=("Segoe UI", 8), justify="left", anchor="w").pack(anchor="w", fill="x", pady=(8,0))
        self.update_idletasks()
        x=anchor.winfo_rootx()+anchor.winfo_width()+6
        y=anchor.winfo_rooty()-4
        sw=self.winfo_screenwidth(); sh=self.winfo_screenheight()
        width=self.winfo_reqwidth(); height=self.winfo_reqheight()
        if x+width>sw-8: x=anchor.winfo_rootx()-width-6
        x=max(8,min(x,sw-width-8)); y=max(8,min(y,sh-height-8))
        self.geometry(f"+{x}+{y}")
        self.deiconify()
        self.bind("<Escape>",lambda e:self.destroy())
        self.bind("<FocusOut>",lambda e:self.destroy())
        self.after(30,self.focus_force)


class AdaptiveFieldRow(ttk.Frame):
    """Responsive Object Editor row.

    Tk's grid geometry can make long World Editor labels/help strings wider than
    the notebook viewport. This row owns its widgets and reflows them according
    to the *actual* available width, so nothing is clipped behind the right pane.
    """
    WIDE_BREAKPOINT = 900
    STACK_BREAKPOINT = 590

    def __init__(self, parent, *, label: str, variable, source_variable, help_text: str = "", popup_help: str = "",
                 editor_type: str = "", actions: list[tuple[str, object]] | None = None):
        super().__init__(parent, padding=(0, 3))
        self._label_text = label
        self._help_text = (help_text or "").strip()
        self._popup_help = (popup_help or help_text or "").strip()
        self._mode = None
        self._balloon = None

        self.name_label = ttk.Label(self, text=label, justify="left", anchor="nw")
        if editor_type == "bool":
            self.entry = ttk.Combobox(self, textvariable=variable, values=("False", "True"), state="readonly")
        else:
            self.entry = ttk.Entry(self, textvariable=variable)

        self.actions = ttk.Frame(self)
        for text_, command in actions or []:
            ttk.Button(self.actions, text=text_, command=command).pack(side="left", padx=(0, 5))
        self.help_button=ttk.Button(self.actions,text="?",width=2,command=self.toggle_help)
        self.help_button.pack(side="left",padx=(0,5))

        self.source_label = ttk.Label(
            self, textvariable=source_variable, style="Source.TLabel",
            justify="left", anchor="nw"
        )
        self.help_label = ttk.Label(
            self, text=self._help_text, style="Source.TLabel",
            justify="left", anchor="nw"
        )

        self.columnconfigure(1, weight=1)
        self.bind("<Configure>", self._on_resize, add="+")
        self.after_idle(self._relayout)

    def toggle_help(self):
        try:
            if self._balloon and self._balloon.winfo_exists():
                self._balloon.destroy(); self._balloon=None; return
        except Exception:
            self._balloon=None
        self._balloon=HelpBalloon(self.help_button,title=self._label_text,message=self._popup_help or f"Controla {self._label_text}.")
        self._balloon.bind("<Destroy>",lambda e:setattr(self,"_balloon",None),add="+")

    def _on_resize(self, event):
        self._relayout(event.width)

    def _clear_grid(self):
        for widget in (self.name_label, self.entry, self.actions, self.source_label, self.help_label):
            widget.grid_forget()

    def _relayout(self, width: int | None = None):
        width = int(width or self.winfo_width() or 1)
        if width <= 1:
            return
        mode = "stack" if width < self.STACK_BREAKPOINT else ("medium" if width < self.WIDE_BREAKPOINT else "wide")

        # Wrap lengths are pixels, not characters. Keep a small safety margin so
        # font/DPI changes never push text under the pane divider.
        safe = max(180, width - 28)
        label_wrap = max(150, min(270, int(width * (0.36 if mode != "stack" else 0.95))))
        self.name_label.configure(wraplength=label_wrap)
        self.source_label.configure(wraplength=safe if mode != "wide" else max(150, int(width * .23)))
        self.help_label.configure(wraplength=safe)

        if mode == self._mode:
            return
        self._mode = mode
        self._clear_grid()
        for col in range(5):
            self.columnconfigure(col, weight=0, minsize=0)

        if mode == "stack":
            self.columnconfigure(0, weight=1)
            self.name_label.grid(row=0, column=0, sticky="ew", pady=(1, 3))
            self.entry.grid(row=1, column=0, sticky="ew")
            if self.actions.winfo_children():
                self.actions.grid(row=2, column=0, sticky="w", pady=(5, 0))
                info_row = 3
            else:
                info_row = 2
            self.source_label.grid(row=info_row, column=0, sticky="ew", pady=(4, 0))
            if self._help_text:
                self.help_label.grid(row=info_row + 1, column=0, sticky="ew", pady=(2, 1))
        elif mode == "medium":
            self.columnconfigure(1, weight=1)
            self.name_label.grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=3)
            self.entry.grid(row=0, column=1, sticky="ew", pady=3)
            if self.actions.winfo_children():
                self.actions.grid(row=1, column=1, sticky="w", pady=(2, 0))
                info_row = 2
            else:
                info_row = 1
            self.source_label.grid(row=info_row, column=0, columnspan=2, sticky="ew", pady=(3, 0))
            if self._help_text:
                self.help_label.grid(row=info_row + 1, column=0, columnspan=2, sticky="ew", pady=(1, 2))
        else:
            self.columnconfigure(1, weight=1)
            self.name_label.grid(row=0, column=0, sticky="nw", padx=(0, 10), pady=3)
            self.entry.grid(row=0, column=1, sticky="ew", pady=3)
            col = 2
            if self.actions.winfo_children():
                self.actions.grid(row=0, column=col, sticky="w", padx=(7, 0)); col += 1
            self.source_label.grid(row=0, column=col, sticky="nw", padx=(8, 0), pady=3)
            if self._help_text:
                self.help_label.grid(row=1, column=0, columnspan=col + 1, sticky="ew", pady=(0, 2))
