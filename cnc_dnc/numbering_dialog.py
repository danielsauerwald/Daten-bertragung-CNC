"""Fenster „Satznummern“: Schema wählen, Vorschau ansehen, nummerieren."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Optional

from .dialogs import Dialog
from .numbering import RenumberResult, renumber
from .textutil import is_tool_call
from .theme import C, Fonts, px

OPTIONS = [
    ("tool", "Pro Werkzeug", "N1000 T1 …   N2000 T2 …",
     "Empfohlen: Die Nummer zeigt das Werkzeug – nach Werkzeugbruch z. B. bei N3000 wieder einsteigen."),
    ("flat", "Durchgehend", "N10  N20  N30 …",
     "Klassisch durchgezählt, jedes Programm beginnt wieder bei N10."),
    ("toolonly", "Nur Werkzeugwechsel", "N1 T1 …   N2 T2 …",
     "Nur die Werkzeugzeilen bekommen eine Nummer – kurz und übersichtlich."),
    ("remove", "Nummern entfernen", "",
     "Sinumerik braucht keine Satznummern; ohne sie wird das Programm kürzer."),
]
STEPS = ["10", "5", "2", "1"]


class RenumberDialog(Dialog):
    """Ergebnis: (Schema, Schrittweite, RenumberResult) oder None."""

    def __init__(self, parent, fonts: Fonts, text: str, dialect: Optional[str], scheme: str, step: int) -> None:
        super().__init__(parent, fonts, "Satznummern")
        self.resizable(False, False)
        self.text = text
        self.dialect = dialect
        self.scheme_var = tk.StringVar(master=self, value=scheme if scheme in [o[0] for o in OPTIONS] else "tool")
        self.step_var = tk.StringVar(master=self, value=str(step) if str(step) in STEPS else "10")
        self.preview_result: Optional[RenumberResult] = None
        bg = C["surface"]

        body = tk.Frame(self, bg=bg, padx=px(self, 24), pady=px(self, 16))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        tk.Label(body, text="Satznummern", bg=bg, fg=C["text"], font=fonts.title).grid(
            row=0, column=0, columnspan=2, sticky="w")
        row = 1
        if dialect == "heidenhain":
            tk.Label(body, text="Heidenhain-Klartext: Die Sätze werden von 0 an lückenlos durchgezählt.",
                     bg=bg, fg=C["muted"], font=fonts.ui).grid(row=row, column=0, columnspan=2, sticky="w",
                                                                pady=(px(self, 6), px(self, 6)))
            row += 1
        else:
            for key, title, example, hint in OPTIONS:
                ttk.Radiobutton(body, text=title, value=key, variable=self.scheme_var, style="Big.TRadiobutton",
                                command=self._update).grid(row=row, column=0, sticky="w", pady=(px(self, 6), 0))
                tk.Label(body, text=example, bg=bg, fg=C["primary"], font=fonts.gutter).grid(
                    row=row, column=1, sticky="w", padx=(px(self, 16), 0), pady=(px(self, 6), 0))
                tk.Label(body, text=hint, bg=bg, fg=C["muted"], font=fonts.small, anchor="w", justify="left").grid(
                    row=row + 1, column=0, columnspan=2, sticky="w", padx=(px(self, 30), 0))
                row += 2
            step_row = tk.Frame(body, bg=bg)
            step_row.grid(row=row, column=0, columnspan=2, sticky="w", pady=(px(self, 12), 0))
            tk.Label(step_row, text="Schrittweite", bg=bg, fg=C["text"], font=fonts.ui).pack(side="left")
            self.step_cb = ttk.Combobox(step_row, textvariable=self.step_var, values=STEPS, state="readonly",
                                        width=4, font=fonts.ui)
            self.step_cb.pack(side="left", padx=(px(self, 10), 0))
            self.step_cb.bind("<<ComboboxSelected>>", lambda e: self._update())
            tk.Label(step_row, text="(10 lässt Platz, um später Sätze dazwischen zu schreiben)", bg=bg,
                     fg=C["muted"], font=fonts.small).pack(side="left", padx=(px(self, 10), 0))
            row += 1

        tk.Label(body, text="VORSCHAU", bg=bg, fg=C["faint"], font=fonts.small_bold).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(px(self, 12), px(self, 2)))
        row += 1
        self.preview = tk.Text(body, height=8, width=64, font=fonts.gutter, bg=C["surface_alt"], fg=C["text"],
                               relief="flat", bd=0, highlightthickness=1, highlightbackground=C["border"],
                               padx=px(self, 8), pady=px(self, 6), wrap="none")
        self.preview.grid(row=row, column=0, columnspan=2, sticky="ew")
        row += 1
        self.message = tk.Label(body, text="", bg=bg, fg=C["muted"], font=fonts.ui, anchor="w", justify="left",
                                wraplength=px(self, 620))
        self.message.grid(row=row, column=0, columnspan=2, sticky="w", pady=(px(self, 8), 0))

        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        self.ok_btn = ttk.Button(bar, text="Nummerieren", style="Primary.TButton", command=self.ok)
        self.ok_btn.pack(side="right", padx=(0, px(self, 12)), pady=px(self, 14))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        self._update()

    def _scheme(self) -> str:
        return self.scheme_var.get()

    def _update(self) -> None:
        scheme = self._scheme()
        try:
            step = int(self.step_var.get())
        except ValueError:
            step = 10
        if hasattr(self, "step_cb"):
            self.step_cb.configure(state="readonly" if scheme in ("tool", "flat") else "disabled")
        res = renumber(self.text, scheme, step, self.dialect)
        self.preview_result = res
        shown = (res.text if not res.error else self.text).split("\n")
        start = 0
        for i, ln in enumerate(shown):
            if ln.strip() and is_tool_call(ln):
                start = max(0, i - 2)
                break
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "\n".join(shown[start:start + 8]))
        self.preview.configure(state="disabled")
        if res.error:
            self.message.configure(text="✕  " + res.error, fg=C["error"])
            self.ok_btn.state(["disabled"])
            return
        self.ok_btn.state(["!disabled"])
        if scheme == "remove" and self.dialect != "heidenhain":
            msg = "Alle Satznummern werden entfernt."
        else:
            msg = "{} Sätze bekommen eine Nummer.".format(res.numbered)
        if res.refs:
            msg += "  {} Verweise (z. B. G71 P/Q, GOTO) werden automatisch angepasst.".format(res.refs)
        if res.note:
            msg += "  " + res.note
        self.message.configure(text=msg, fg=C["ok"])

    def ok(self) -> None:
        res = self.preview_result
        if res is None or res.error:
            return
        try:
            step = int(self.step_var.get())
        except ValueError:
            step = 10
        self.result = (self._scheme(), step, res)
        self.destroy()
