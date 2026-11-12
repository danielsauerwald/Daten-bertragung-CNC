"""Fenster „Postprozessor“: G- und M-Befehle einer Maschine einstellen, mit Vorschau."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, Optional

from . import dialogs
from .dialogs import Dialog
from .kontur import beispiel_teil, generate
from .postpro import ARTEN, FELDER, VORLAGEN_NAMEN, Postprozessor, fmt, vorlage
from .system import IS_WIN, toggle_touch_keyboard
from .theme import C, MONO_FAMILY, Fonts, px

TABS = (("format", "Format"), ("drehen", "Drehen"), ("achsen", "C-/Y-Achse, angetr. WZ"))


def vorschau(pp: Postprozessor) -> str:
    """Auszug aus dem Beispielteil: Kopf, erstes Drehwerkzeug, erste Fräsung (0° und 180°), Ende."""
    res = generate(beispiel_teil(), rahmen=True, pp=pp, maschine="BEISPIEL")
    if not res.ok:
        return "\n".join("✕ " + e for e in res.errors)
    blocks = [[]]  # Arbeitsgänge sind durch Leerzeilen getrennt
    for ln in res.code.rstrip("\n").split("\n"):
        if ln.strip():
            blocks[-1].append(ln)
        else:
            blocks.append([])
    head, turn, end = blocks[0], blocks[1], blocks[-1]
    mill = next((b for b in blocks if b and "FRAESEN" in b[0]), [])
    out = head + [""] + turn[:5] + ["…"] + turn[-3:]
    if mill:
        def pos(w: float) -> int:
            first = (pp.fill(pp.c_pos, W=w) or [""])[0]
            return mill.index(first) if first in mill else -1

        i0, i1 = pos(0), pos(180)
        out += [""] + mill[:(i0 + 4 if i0 >= 0 else 6)] + ["…"]
        if i1 > 0:
            out += mill[i1:i1 + 1] + ["…"]
        out += mill[-7:]
    if end is not turn and end is not mill:
        out += [""] + end
    return "\n".join(out)


class PostproDialog(Dialog):
    """Ergebnis: Postprozessor oder None."""

    def __init__(self, parent, fonts: Fonts, machine_name: str, pp: Postprozessor) -> None:
        super().__init__(parent, fonts, "Postprozessor – " + machine_name)
        self.pp = pp.copy()
        self.vars: Dict[str, tk.Variable] = {}
        self._loading = False
        self._job: Optional[str] = None
        bg = C["bg"]
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self._size = (min(sw - 24, px(self, 1240)), min(sh - 72, px(self, 700)))

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x", padx=px(self, 16), pady=(px(self, 12), px(self, 6)))
        tk.Label(head, text="Postprozessor  ·  " + machine_name, bg=bg, fg=C["text"], font=fonts.title).pack(
            side="left")
        self.preset = tk.StringVar(master=self, value=self._matching_preset(self.pp))
        ttk.Button(head, text="Vorlage übernehmen", style="Small.TButton", command=self.load_preset).pack(
            side="right")
        ttk.Combobox(head, textvariable=self.preset, values=list(VORLAGEN_NAMEN.values()), state="readonly",
                     width=42, font=fonts.ui).pack(side="right", padx=(0, px(self, 8)))
        tk.Label(head, text="Vorlage:", bg=bg, fg=C["muted"], font=fonts.ui).pack(side="right",
                                                                                   padx=(0, px(self, 6)))

        main = tk.Frame(self, bg=bg)
        main.pack(fill="both", expand=True, padx=px(self, 16))
        main.grid_columnconfigure(0, weight=3)
        main.grid_columnconfigure(1, weight=2)
        main.grid_rowconfigure(0, weight=1)
        self.nb = ttk.Notebook(main)
        self.nb.grid(row=0, column=0, sticky="nsew")
        self.tabs: Dict[str, tk.Frame] = {}
        for key, title in TABS:
            f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 14), pady=px(self, 10))
            f.grid_columnconfigure(1, weight=1)
            self.nb.add(f, text=title)
            self.tabs[key] = f
        rows = {k: 0 for k, _ in TABS}
        for key, label, hint, tab in FELDER:
            f = self.tabs[tab]
            r = rows[tab]
            rows[tab] += 1
            value = getattr(self.pp, key)
            if isinstance(value, bool):
                var: tk.Variable = tk.BooleanVar(master=self, value=value)
                ttk.Checkbutton(f, text=label, variable=var, style="Surface.TCheckbutton",
                                command=self.changed).grid(row=r, column=0, columnspan=3, sticky="w",
                                                           pady=px(self, 3))
                self.vars[key] = var
                continue
            tk.Label(f, text=label, bg=C["surface"], fg=C["text"], font=fonts.ui, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, px(self, 12)), pady=px(self, 3))
            var = tk.StringVar(master=self, value=self._text(key, value))
            self.vars[key] = var
            if key == "art":
                w = ttk.Combobox(f, textvariable=var, values=list(ARTEN.values()), state="readonly", width=20,
                                 font=fonts.ui)
                w.bind("<<ComboboxSelected>>", lambda e: self.changed())
            else:
                w = ttk.Entry(f, textvariable=var, width=26, font=(MONO_FAMILY, 11))
                var.trace_add("write", lambda *a: self.changed())
            w.grid(row=r, column=1, sticky="ew", pady=px(self, 3))
            if hint:
                tk.Label(f, text=hint, bg=C["surface"], fg=C["muted"], font=fonts.small, anchor="w").grid(
                    row=r, column=2, sticky="w", padx=(px(self, 10), 0))
        if IS_WIN:
            ttk.Button(self.tabs["format"], text="⌨ Tastatur", style="Small.TButton",
                       command=toggle_touch_keyboard).grid(row=rows["format"], column=0, sticky="w",
                                                           pady=(px(self, 10), 0))

        right = tk.Frame(main, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        right.grid(row=0, column=1, sticky="nsew", padx=(px(self, 12), 0))
        tk.Label(right, text="VORSCHAU (Beispielteil)", bg=C["surface"], fg=C["faint"],
                 font=fonts.small_bold).pack(anchor="w", padx=px(self, 10), pady=(px(self, 8), 0))
        box = tk.Frame(right, bg=C["surface"])
        box.pack(fill="both", expand=True, padx=px(self, 6), pady=px(self, 6))
        self.preview = tk.Text(box, font=(MONO_FAMILY, 10), wrap="none", bg=C["surface_alt"], fg=C["text"],
                               relief="flat", highlightthickness=0, padx=px(self, 8), pady=px(self, 6))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")  # zuerst, sonst nimmt das Textfeld die ganze Breite
        self.preview.configure(width=20)
        self.preview.pack(side="left", fill="both", expand=True)

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", padx=px(self, 16), pady=px(self, 12))
        bar.grid_columnconfigure(0, weight=1)
        self.msg = tk.Label(bar, text="", bg=bg, fg=C["muted"], font=fonts.ui, anchor="w", justify="left",
                            wraplength=px(self, 760))
        self.msg.grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Abbrechen", command=self.cancel).grid(row=0, column=1, padx=(px(self, 8), 0))
        self.ok_btn = ttk.Button(bar, text="Speichern", style="Primary.TButton", command=self.ok)
        self.ok_btn.grid(row=0, column=2, padx=(px(self, 8), 0))
        self.update_view()

    def show(self):
        w, h = self._size
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry("{}x{}+{}+{}".format(w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 2 - 20)))
        return super().show()

    @staticmethod
    def _matching_preset(pp: Postprozessor) -> str:
        """Name der Vorlage, der die Einstellung entspricht (Wechselpunkt egal), sonst leer."""
        mine = pp.to_dict()
        for key, name in VORLAGEN_NAMEN.items():
            ref = vorlage(key).to_dict()
            if all(mine[k] == ref[k] for k in ref if k not in ("wechsel_x", "wechsel_z")):
                return name
        return ""

    @staticmethod
    def _text(key: str, value) -> str:
        if key == "art":
            return ARTEN.get(value, value)
        if isinstance(value, float):
            return fmt(value)
        return str(value)

    def _set_fields(self, pp: Postprozessor) -> None:
        self._loading = True
        for key, var in self.vars.items():
            var.set(getattr(pp, key) if isinstance(var, tk.BooleanVar) else self._text(key, getattr(pp, key)))
        self._loading = False
        self.changed()

    def load_preset(self) -> None:
        key = {v: k for k, v in VORLAGEN_NAMEN.items()}.get(self.preset.get())
        if key is None:
            dialogs.info(self, self.fonts, "Vorlage wählen", "Bitte oben eine Vorlage auswählen.")
            return
        if not dialogs.confirm(self, self.fonts, "Vorlage übernehmen",
                               "Alle Felder mit der Vorlage „{}“ überschreiben?".format(self.preset.get()),
                               yes="Übernehmen"):
            return
        new = vorlage(key)
        new.wechsel_x, new.wechsel_z = self.pp.wechsel_x, self.pp.wechsel_z  # Maschinenmaße behalten
        self._set_fields(new)

    def collect(self) -> Postprozessor:
        """Felder -> Postprozessor (Zahlenfehler: ValueError)."""
        pp = self.pp.copy()
        for key, var in self.vars.items():
            v = var.get()
            if key == "art":
                pp.art = {b: a for a, b in ARTEN.items()}.get(v, "sinumerik")
            elif isinstance(var, tk.BooleanVar):
                setattr(pp, key, bool(v))
            elif isinstance(getattr(pp, key), float):
                setattr(pp, key, float(str(v).replace(",", ".")))
            else:
                setattr(pp, key, str(v).strip())
        return pp

    def changed(self) -> None:
        if self._loading:
            return
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(200, self.update_view)

    def update_view(self) -> None:
        self._job = None
        try:
            pp = self.collect()
        except ValueError:
            self.msg.configure(text="✕  Werkzeugwechselpunkt X/Z muss eine Zahl sein.", fg=C["error"])
            self.ok_btn.state(["disabled"])
            return
        err, warn = pp.pruefen()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", vorschau(pp))
        self.preview.configure(state="disabled")
        if err:
            self.msg.configure(text="✕  " + err[0], fg=C["error"])
        elif warn:
            self.msg.configure(text="!  " + warn[0], fg=C["warn"])
        else:
            self.msg.configure(text="Platzhalter: {T} {T2} Werkzeug · {D} {D2} Korrektur · {S} Drehzahl · "
                                    "{W} Winkel · | neue Zeile", fg=C["muted"])
        self.ok_btn.state(["disabled"] if err else ["!disabled"])

    def ok(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        try:
            pp = self.collect()
        except ValueError:
            return
        if pp.pruefen()[0]:
            self.update_view()
            return
        self.result = pp
        self.destroy()
