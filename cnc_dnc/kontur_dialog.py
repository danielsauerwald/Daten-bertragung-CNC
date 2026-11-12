"""Fenster „Kontur → CNC-Code“: Teil eingeben, Elemente antippen, Bearbeitung wählen, Code erzeugen."""

from __future__ import annotations

import copy
import math
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from . import dialogs
from .dialogs import Dialog, FormDialog
from .kontur import (
    ECKEN,
    FEATURE_LAGE,
    FEATURES,
    SEG_OPS,
    Ergebnis,
    Fraesung,
    Geometrie,
    KPunkt,
    Teil,
    Werkzeug,
    beispiel_teil,
    build_geometry,
    contour_radius,
    fmt,
    generate,
    gleich_verteilt,
    masse,
    neues_teil,
    parse_num,
    parse_winkel,
    polyline,
    winkel_text,
)
from .postpro import ARTEN, Postprozessor, fuer_maschine
from .system import IS_WIN, toggle_touch_keyboard
from .theme import C, MONO_FAMILY, Fonts, px

FEAT_COLOR = "#7A3EB1"
SEL_COLOR = "#F08C00"
PATH_G0 = "#D9480F"
PATH_G1 = "#0E8A5B"


def _inv(d: Dict[str, str]) -> Dict[str, str]:
    return {v: k for k, v in d.items()}


def _clip(a: Tuple[float, float], b: Tuple[float, float], x_max: float):
    """Strecke auf den Bereich links von x_max kürzen (Liang-Barsky, nur rechte Kante)."""
    (x0, y0), (x1, y1) = a, b
    if x0 > x_max and x1 > x_max:
        return None
    if x0 <= x_max and x1 <= x_max:
        return (x0, y0, x1, y1)
    t = (x_max - x0) / (x1 - x0)
    xm, ym = x_max, y0 + t * (y1 - y0)
    return (x0, y0, xm, ym) if x0 <= x_max else (xm, ym, x1, y1)


def _feature_text(fr: Fraesung) -> str:
    if fr.art == "flaeche":
        return "SW {}  ·  Z{} … Z{}".format(fmt(fr.mass), fmt(max(fr.z1, fr.z2)), fmt(min(fr.z1, fr.z2)))
    if fr.art == "nut":
        return "{} × {} tief  ·  Z{} … Z{}".format(fmt(fr.mass), fmt(fr.tiefe), fmt(max(fr.z1, fr.z2)),
                                                 fmt(min(fr.z1, fr.z2)))
    if fr.art == "bohrung_radial":
        return "Ø{} × {}  ·  Z{}".format(fmt(fr.mass), fmt(fr.tiefe), fmt(fr.z1))
    return "Ø{} × {}  ·  Lochkreis Ø{}".format(fmt(fr.mass), fmt(fr.tiefe), fmt(fr.teilkreis))


def _ecke_text(p: KPunkt, teil: Teil) -> str:
    if p.ecke == "fase":
        return "Fase {}".format(fmt(p.wert))
    if p.ecke == "radius":
        return "R{}".format(fmt(p.wert))
    if p.ecke == "entgraten":
        return "Entgraten ({})".format(fmt(teil.kante))
    return ""


def _tool_choices(teil: Teil, arten, lage: str = "") -> List[str]:
    out = []
    for w in sorted(teil.werkzeuge, key=lambda w: w.t):
        if w.art in arten and (not lage or not w.lage or w.lage == lage):
            out.append(w.label)
    return out


def _tool_from_choice(text: str) -> Optional[int]:
    text = (text or "").strip()
    if text.startswith("T"):
        num = ""
        for ch in text[1:]:
            if not ch.isdigit():
                break
            num += ch
        if num:
            return int(num)
    return None


# ==========================================================================
# Formular für eine Fräsung
# ==========================================================================


class FeatureDialog(Dialog):
    """Ergebnis: Fraesung oder None."""

    def __init__(self, parent, fonts: Fonts, teil: Teil, fr: Fraesung, title: str) -> None:
        super().__init__(parent, fonts, title)
        self.resizable(False, False)
        self.teil = teil
        self.fr = copy.deepcopy(fr)
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 22), pady=px(self, 16))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        self.body = body
        tk.Label(body, text=title, bg=bg, fg=C["text"], font=fonts.title, anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, px(self, 8)))
        self.vars: Dict[str, tk.StringVar] = {}
        self._row = 1
        art = fr.art
        if art in ("flaeche", "nut"):
            self._entry("z1", "Z rechts (Anfang)", fmt(fr.z1), "0 = läuft an der Stirnseite aus")
            self._entry("z2", "Z links (Ende)", fmt(fr.z2))
        if art == "flaeche":
            self._entry("mass", "Schlüsselweite / Maß über Fläche", fmt(fr.mass),
                        "Abstand Fläche – Mitte × 2")
        elif art == "nut":
            self._entry("mass", "Breite", fmt(fr.mass))
            self._entry("tiefe", "Tiefe", fmt(fr.tiefe), "ab Außendurchmesser")
        elif art == "bohrung_radial":
            self._entry("z1", "Z der Bohrung (Mitte)", fmt(fr.z1))
            self._entry("mass", "Durchmesser", fmt(fr.mass))
            self._entry("tiefe", "Tiefe (Spitze)", fmt(fr.tiefe), "ab Außendurchmesser")
        elif art == "bohrung_axial":
            self._entry("teilkreis", "Lochkreis-Ø", fmt(fr.teilkreis), "0 = Mitte")
            self._entry("mass", "Durchmesser", fmt(fr.mass))
            self._entry("tiefe", "Tiefe (Spitze)", fmt(fr.tiefe))
            self._entry("z1", "Z der Oberfläche", fmt(fr.z1), "meist 0 (Stirnseite)")
        self._entry("winkel", "Winkel C (°)", winkel_text(fr.winkel), "z. B. 0 180  oder  0 120 240")
        quick = tk.Frame(body, bg=bg)
        quick.grid(row=self._row, column=1, columnspan=2, sticky="w", pady=(0, px(self, 6)))
        tk.Label(quick, text="gleichmäßig:", bg=bg, fg=C["muted"], font=fonts.small).pack(side="left")
        for n in (1, 2, 3, 4, 6):
            ttk.Button(quick, text="{}×".format(n), style="Small.TButton", width=3,
                       command=lambda n=n: self._spread(n)).pack(side="left", padx=(px(self, 6), 0))
        self._row += 1
        arten = ("bohrer", "fraeser") if art.startswith("bohrung") else ("fraeser",)
        lage = FEATURE_LAGE[art]
        choices = _tool_choices(teil, arten, lage)
        cur = teil.tool(fr.wz)
        self._combo("wz", "Werkzeug", cur.label if cur else (choices[0] if choices else ""), choices)
        self.deburr = tk.BooleanVar(master=self, value=fr.entgraten)
        ttk.Checkbutton(body, text="Kanten entgraten (Fasenfräser 90°)", variable=self.deburr,
                        style="Surface.TCheckbutton", command=self._toggle).grid(
            row=self._row, column=0, columnspan=3, sticky="w", pady=(px(self, 6), 0))
        self._row += 1
        dchoices = _tool_choices(teil, ("fase",), lage)
        dcur = teil.tool(fr.wz_entgraten)
        self.deburr_widgets = []
        self._combo("wz_entgraten", "Entgratwerkzeug", dcur.label if dcur else (dchoices[0] if dchoices else ""),
                    dchoices, keep=self.deburr_widgets)
        self._entry("fase", "Fase", fmt(fr.fase), keep=self.deburr_widgets)
        self.msg = tk.Label(body, text="", bg=bg, fg=C["error"], font=fonts.ui, anchor="w", justify="left",
                            wraplength=px(self, 520))
        self.msg.grid(row=self._row, column=0, columnspan=3, sticky="w", pady=(px(self, 6), 0))
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        ttk.Button(bar, text="Übernehmen", style="Primary.TButton", command=self.ok).pack(
            side="right", padx=(0, px(self, 12)), pady=px(self, 12))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        self.bind("<Return>", lambda e: self.ok())
        self._toggle()

    def _label(self, text: str, hint: str = "") -> tk.Frame:
        bg = C["surface"]
        lab = tk.Frame(self.body, bg=bg)
        lab.grid(row=self._row, column=0, sticky="w", padx=(0, px(self, 14)), pady=px(self, 4))
        tk.Label(lab, text=text, bg=bg, fg=C["text"], font=self.fonts.ui, anchor="w").pack(anchor="w")
        if hint:
            tk.Label(lab, text=hint, bg=bg, fg=C["muted"], font=self.fonts.small, anchor="w").pack(anchor="w")
        return lab

    def _entry(self, key: str, label: str, value: str, hint: str = "", keep=None) -> None:
        lab = self._label(label, hint)
        var = tk.StringVar(master=self, value=value)
        self.vars[key] = var
        e = ttk.Entry(self.body, textvariable=var, font=self.fonts.title, width=16)
        e.grid(row=self._row, column=1, sticky="w", pady=px(self, 4))
        if keep is not None:
            keep.extend([lab, e])
        if IS_WIN:
            b = ttk.Button(self.body, text="⌨", style="Small.TButton", width=3,
                           command=toggle_touch_keyboard)
            b.grid(row=self._row, column=2, padx=(px(self, 8), 0))
            if keep is not None:
                keep.append(b)
        self._row += 1

    def _combo(self, key: str, label: str, value: str, choices: List[str], keep=None) -> None:
        lab = self._label(label)
        var = tk.StringVar(master=self, value=value)
        self.vars[key] = var
        cb = ttk.Combobox(self.body, textvariable=var, values=choices, state="readonly",
                          font=self.fonts.ui, width=24)
        cb.grid(row=self._row, column=1, columnspan=2, sticky="w", pady=px(self, 4))
        if keep is not None:
            keep.extend([lab, cb])
        self._row += 1

    def _toggle(self) -> None:
        for w in self.deburr_widgets:
            if self.deburr.get():
                w.grid()
            else:
                w.grid_remove()

    def _spread(self, n: int) -> None:
        try:
            start = parse_winkel(self.vars["winkel"].get())[0]
        except ValueError:
            start = 0.0
        self.vars["winkel"].set(winkel_text(gleich_verteilt(n, start)))

    def ok(self) -> None:
        fr = self.fr
        try:
            for key in ("z1", "z2", "mass", "tiefe", "teilkreis", "fase"):
                if key in self.vars and (key != "fase" or self.deburr.get()):
                    setattr(fr, key, parse_num(self.vars[key].get()))
        except ValueError:
            self.msg.configure(text="Bitte nur Zahlen eintragen (Komma oder Punkt).")
            return
        try:
            fr.winkel = parse_winkel(self.vars["winkel"].get())
        except ValueError:
            self.msg.configure(text="Winkel fehlen, z. B. „0 180“.")
            return
        wz = _tool_from_choice(self.vars["wz"].get())
        if wz is None:
            self.msg.configure(text="Bitte ein Werkzeug wählen (Liste im Reiter „Werkzeuge“).")
            return
        fr.wz = wz
        fr.entgraten = bool(self.deburr.get())
        if fr.entgraten:
            wd = _tool_from_choice(self.vars["wz_entgraten"].get())
            if wd is None:
                self.msg.configure(text="Bitte einen Fasenfräser wählen (Reiter „Werkzeuge“).")
                return
            fr.wz_entgraten = wd
        self.result = fr
        self.destroy()


# ==========================================================================
# Hauptfenster des Assistenten
# ==========================================================================


class KonturDialog(Dialog):
    """Ergebnis: ("neu" | "einfuegen", code) oder None."""

    closes_keyboard = True

    def __init__(self, parent, fonts: Fonts, teil: Teil, machines: Optional[list] = None, machine_id: str = "",
                 on_machines_changed=None) -> None:
        """``machines``: Maschinenliste (MachineProfile) – deren Postprozessoren werden benutzt und beim
        Bearbeiten gespeichert (``on_machines_changed``)."""
        super().__init__(parent, fonts, "Kontur → CNC-Code")
        self.teil = teil  # wird direkt bearbeitet; der Aufrufer speichert es danach
        self.machines = list(machines or [])
        self.on_machines_changed = on_machines_changed
        self.machine = next((m for m in self.machines if m.id == machine_id), None) or \
            (self.machines[0] if self.machines else None)
        self.pp: Postprozessor = fuer_maschine(self.machine) if self.machine else Postprozessor()
        self.sel: Optional[Tuple[str, int]] = None  # ("seg"|"corner"|"feat", Nummer)
        self.geom: Geometrie = build_geometry(teil)
        self.res: Ergebnis = Ergebnis()
        self._job: Optional[str] = None
        self._loading = False
        self.show_path = tk.BooleanVar(master=self, value=True)
        # Wunschgröße; show() verkleinert auf den freien Bildschirm (ohne Taskleiste und Titelleiste)
        self._size = (px(self, 1600), px(self, 1000))
        self.minsize(px(self, 800), px(self, 500))

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._build_head()
        main = tk.Frame(self, bg=C["bg"])
        main.grid(row=1, column=0, sticky="nsew", padx=px(self, 12))
        main.grid_rowconfigure(0, weight=1)
        main.grid_columnconfigure(0, weight=1)
        self._build_canvas(main)
        self._build_tabs(main)
        self._build_bottom()
        self._load_fields()
        self.refresh()

    def show(self):
        """Wie Dialog.show, aber groß: so viel vom freien Bildschirm wie möglich (ohne Taskleiste)."""
        w, h, x, y = self.place_on_screen(*self._size)
        self.geometry("{}x{}+{}+{}".format(w, h, x, y))
        self.deiconify()
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.lift()
        self.focus_force()
        self.wait_window()
        return self.result

    # ------------------------------------------------------------------ Aufbau

    def _build_head(self) -> None:
        bg = C["bg"]
        head = tk.Frame(self, bg=bg)
        head.grid(row=0, column=0, sticky="ew", padx=px(self, 16), pady=(px(self, 10), px(self, 6)))
        tk.Label(head, text="Kontur → CNC-Code", bg=bg, fg=C["text"], font=self.fonts.title).pack(side="left")
        self.head_sub = tk.Label(head, text="", bg=bg, fg=C["muted"], font=self.fonts.small)
        self.head_sub.pack(side="left", padx=(px(self, 14), 0))
        ttk.Button(head, text="Beispiel", style="Small.TButton", command=self.load_example).pack(side="right")
        ttk.Button(head, text="Neu …", style="Small.TButton", command=self.new_part).pack(
            side="right", padx=(0, px(self, 8)))

    def _build_canvas(self, main: tk.Frame) -> None:
        left = tk.Frame(main, bg=C["bg"])
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)
        self.cv = tk.Canvas(left, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"],
                            cursor="hand2")
        self.cv.grid(row=0, column=0, sticky="nsew")
        self.cv.bind("<Configure>", lambda e: self.redraw())
        self.cv.bind("<ButtonRelease-1>", self._click)
        self.selbar = tk.Frame(left, bg=C["surface"], highlightthickness=1, highlightbackground=C["border"])
        self.selbar.grid(row=1, column=0, sticky="ew", pady=(px(self, 8), 0))
        self.sel_label = tk.Label(self.selbar, text="", bg=C["surface"], fg=C["text"], font=self.fonts.ui_bold,
                                  anchor="w", justify="left")
        self.sel_label.pack(fill="x", padx=px(self, 10), pady=(px(self, 6), px(self, 2)))
        self.sel_buttons = tk.Frame(self.selbar, bg=C["surface"])
        self.sel_buttons.pack(fill="x", padx=px(self, 8), pady=(0, px(self, 8)))

    def _build_tabs(self, main: tk.Frame) -> None:
        right = tk.Frame(main, bg=C["bg"], width=px(self, 510))
        right.grid(row=0, column=1, sticky="nsew", padx=(px(self, 12), 0))
        right.grid_propagate(False)
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)
        style = ttk.Style(self)
        style.configure("Kontur.TNotebook", background=C["bg"], borderwidth=0)
        style.configure("Kontur.TNotebook.Tab", padding=(px(self, 11), px(self, 9)))
        # Tasten der Auswahlleiste: so breit wie ihr Text (das Theme setzt sonst eine Mindestbreite)
        style.configure("Bar.TButton", padding=(px(self, 14), px(self, 10)))
        style.configure("BarPrimary.TButton", padding=(px(self, 14), px(self, 10)))
        for name in ("Bar.TButton", "BarPrimary.TButton"):
            base = "Primary.TButton" if name.startswith("BarPrimary") else "TButton"
            for opt in ("background", "foreground", "bordercolor", "lightcolor", "darkcolor", "relief",
                        "focuscolor"):
                v = style.lookup(base, opt)
                if v:
                    style.configure(name, **{opt: v})
            style.map(name, **{k: style.map(base, k) for k in ("background", "lightcolor", "darkcolor",
                                                                 "foreground")})
        self.nb = ttk.Notebook(right, style="Kontur.TNotebook")
        self.nb.grid(row=0, column=0, sticky="nsew")
        self.fvars: Dict[str, tk.StringVar] = {}
        self._tab_kontur()
        self._tab_fraesen()
        self._tab_werkzeuge()
        self._tab_postpro()
        self._tab_code()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: (self._show_code(), self.redraw()))

    def _tab(self, text: str) -> tk.Frame:
        f = tk.Frame(self.nb, bg=C["surface"], padx=px(self, 10), pady=px(self, 8))
        self.nb.add(f, text=text)
        return f

    def _field(self, parent, key: str, label: str, width: int = 7, col: int = 0, row: int = 0,
               conv=float) -> ttk.Entry:
        tk.Label(parent, text=label, bg=C["surface"], fg=C["text"], font=self.fonts.ui).grid(
            row=row, column=col, sticky="w", padx=(0 if col == 0 else px(self, 12), px(self, 6)),
            pady=px(self, 3))
        var = tk.StringVar(master=self)
        self.fvars[key] = var
        e = ttk.Entry(parent, textvariable=var, width=width, font=self.fonts.ui)
        e.grid(row=row, column=col + 1, sticky="w", pady=px(self, 3))
        var.trace_add("write", lambda *a, k=key, c=conv: self._field_changed(k, c))
        return e

    def _tree(self, parent, cols: List[Tuple[str, str, int]], height: int = 6) -> ttk.Treeview:
        fr = tk.Frame(parent, bg=C["surface"])
        tree = ttk.Treeview(fr, columns=[c[0] for c in cols], show="headings", selectmode="browse", height=height)
        for key, title, width in cols:
            tree.heading(key, text=title)
            tree.column(key, width=px(self, width), minwidth=px(self, 30), stretch=key == cols[-1][0]
                        or width > 120, anchor="w")
        sb = ttk.Scrollbar(fr, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        fr.pack(fill="both", expand=True, pady=(px(self, 6), px(self, 6)))
        return tree

    @staticmethod
    def _shrink_list_first(tab: tk.Frame, tree: ttk.Treeview) -> None:
        """Wird der Reiter zu niedrig (kleiner Bildschirm, Taskleiste), soll die Liste kürzer werden –
        nicht die Tasten und Felder darunter. Bei pack schrumpft sonst das zuletzt gepackte Element."""
        box = tree.master
        kids = tab.pack_slaves()
        for w in reversed(kids[kids.index(box) + 1:]):
            w.pack_configure(side="bottom", before=box)

    def _buttons(self, parent, items) -> tk.Frame:
        fr = tk.Frame(parent, bg=C["surface"])
        fr.pack(fill="x")
        for i, (text, cmd) in enumerate(items):
            fr.grid_columnconfigure(i, weight=1, uniform="b")
            ttk.Button(fr, text=text, style="Small.TButton", command=cmd).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(self, 4), 0))
        return fr

    def _tab_kontur(self) -> None:
        f = self._tab("Kontur")
        top = tk.Frame(f, bg=C["surface"])
        top.pack(fill="x")
        self._field(top, "name", "Teil", 14, conv=str)
        self._field(top, "rohteil_d", "Rohteil Ø", 6, col=2)
        self._field(top, "planaufmass", "Planaufmaß", 6, col=0, row=1)
        self._field(top, "rohteil_l", "Rohteil L", 6, col=2, row=1)
        tk.Label(top, text="Rohteil L = Länge inkl. Planaufmaß, 0 = Stange", bg=C["surface"], fg=C["muted"],
                 font=self.fonts.small).grid(row=2, column=0, columnspan=4, sticky="w")
        self.pt_tree = self._tree(f, [("nr", "Nr", 34), ("x", "X (Ø)", 60), ("z", "Z", 60), ("l", "Länge", 62),
                                      ("op", "Bearbeitung", 108), ("ecke", "Ecke", 96)], height=5)
        self.pt_tree.bind("<<TreeviewSelect>>", self._pt_selected)
        self.pt_tree.bind("<Double-1>", lambda e: self.edit_point())
        self._buttons(f, [("+ Absatz …", self.add_step), ("+ Punkt …", self.add_point),
                          ("Ändern …", self.edit_point), ("Löschen", self.delete_point)])
        turn = tk.Frame(f, bg=C["surface"])
        turn.pack(fill="x", pady=(px(self, 8), 0))
        tk.Label(turn, text="DREHEN", bg=C["surface"], fg=C["faint"], font=self.fonts.small_bold).grid(
            row=0, column=0, sticky="w")
        tk.Label(turn, text="Schruppen", bg=C["surface"], font=self.fonts.ui).grid(row=1, column=0, sticky="w")
        self.cb_rough = ttk.Combobox(turn, state="readonly", width=10, font=self.fonts.ui)
        self.cb_rough.grid(row=1, column=1, sticky="w", pady=px(self, 3), padx=(px(self, 6), 0))
        tk.Label(turn, text="Schlichten", bg=C["surface"], font=self.fonts.ui).grid(row=2, column=0, sticky="w")
        self.cb_fine = ttk.Combobox(turn, state="readonly", width=10, font=self.fonts.ui)
        self.cb_fine.grid(row=2, column=1, sticky="w", pady=px(self, 3), padx=(px(self, 6), 0))
        self.cb_rough.bind("<<ComboboxSelected>>", lambda e: self._turn_tools())
        self.cb_fine.bind("<<ComboboxSelected>>", lambda e: self._turn_tools())
        self._field(turn, "aufmass_x", "Aufmaß X (Ø)", 5, col=2, row=1)
        self._field(turn, "aufmass_z", "Aufmaß Z", 5, col=2, row=2)
        self._field(turn, "kante", "Entgraten = Fase", 5, col=2, row=0)
        self._shrink_list_first(f, self.pt_tree)

    def _tab_fraesen(self) -> None:
        f = self._tab("Fräsen")
        tk.Label(f, text="Fräsungen und Bohrungen auf Winkellagen (C-Achse, Y-Achse). Auch über die Zeichnung: "
                         "Element antippen → „Fräsen hier“.", bg=C["surface"], fg=C["muted"], font=self.fonts.small,
                 wraplength=px(self, 460), justify="left", anchor="w").pack(fill="x")
        self.ft_tree = self._tree(f, [("nr", "Nr", 34), ("art", "Art", 128), ("mass", "Maße", 170),
                                      ("winkel", "C (°)", 80), ("entgr", "Entgr.", 50)], height=7)
        self.ft_tree.bind("<<TreeviewSelect>>", self._ft_selected)
        self.ft_tree.bind("<Double-1>", lambda e: self.edit_feature())
        self._buttons(f, [("+ Fläche", lambda: self.add_feature("flaeche")),
                          ("+ Nut", lambda: self.add_feature("nut")),
                          ("+ Bohr. radial", lambda: self.add_feature("bohrung_radial")),
                          ("+ Bohr. Stirn", lambda: self.add_feature("bohrung_axial"))])
        tk.Frame(f, bg=C["surface"], height=px(self, 6)).pack()
        self._buttons(f, [("Ändern …", self.edit_feature), ("Entgraten ein/aus", self.toggle_deburr),
                          ("Löschen", self.delete_feature)])
        self._shrink_list_first(f, self.ft_tree)

    def _tab_werkzeuge(self) -> None:
        f = self._tab("Werkzeuge")
        self.wz_tree = self._tree(f, [("t", "T", 30), ("name", "Bezeichnung", 104), ("halter", "Halter / Platte", 138),
                                      ("art", "Art", 80), ("s", "S", 46), ("f", "F", 42), ("ap", "Zust.", 40)],
                                  height=8)
        self.wz_tree.bind("<Double-1>", lambda e: self.edit_tool())
        self._buttons(f, [("+ Werkzeug …", self.add_tool), ("Ändern …", self.edit_tool),
                          ("Löschen", self.delete_tool)])
        tk.Label(f, text="Halter, Platten, Bohrstangen, Bohrer und Fräser kommen aus dem Katalog (ISO-Bezeichnungen). "
                         "F: Drehen mm/U, Fräsen/Bohren mm/min. Zustellung: Drehen je Seite, Fräsen Tiefe je Schnitt, "
                         "Bohren je Spanbruch (0 = in einem Zug).", bg=C["surface"], fg=C["muted"], font=self.fonts.small,
                 wraplength=px(self, 460), justify="left", anchor="w").pack(fill="x", pady=(px(self, 8), 0))
        self._shrink_list_first(f, self.wz_tree)

    def _tab_postpro(self) -> None:
        f = self._tab("Postprozessor")
        top = tk.Frame(f, bg=C["surface"])
        top.pack(fill="x")
        tk.Label(top, text="Maschine", bg=C["surface"], font=self.fonts.ui).pack(side="left")
        self.machine_cb = ttk.Combobox(top, state="readonly", width=26, font=self.fonts.ui,
                                       values=[m.display_name for m in self.machines])
        if self.machine is not None:
            self.machine_cb.set(self.machine.display_name)
        self.machine_cb.pack(side="left", padx=(px(self, 8), 0))
        self.machine_cb.bind("<<ComboboxSelected>>", lambda e: self._machine_selected())
        self.pp_info = tk.Label(f, text="", bg=C["surface_alt"], fg=C["text"], font=(MONO_FAMILY, 10),
                                anchor="nw", justify="left", padx=px(self, 8), pady=px(self, 6))
        self.pp_info.pack(fill="x", pady=(px(self, 8), px(self, 6)))
        ttk.Button(f, text="Postprozessor bearbeiten …", command=self.edit_postpro).pack(anchor="w")
        g = tk.Frame(f, bg=C["surface"])
        g.pack(fill="x", pady=(px(self, 10), 0))
        self._field(g, "sicherheit", "Sicherheitsabstand (mm)", 6, row=0)
        tk.Label(f, text="Der Postprozessor gehört zur Maschine (auch unter ⚙ Maschinen → Bearbeiten → "
                         "Postprozessor): Werkzeugaufruf, M-Befehle für Spindel, Kühlmittel, angetriebenes Werkzeug "
                         "(z. B. S3=/M3= oder S4=/M4=), C-Achse und Klemmung, Ebenen, Vorschubart, Programmanfang "
                         "und -ende, Werkzeugwechselpunkt.\n\nBitte jedes neue Programm zuerst simulieren bzw. mit "
                         "Vorschub-Override vorsichtig einfahren.", bg=C["surface"], fg=C["muted"],
                 font=self.fonts.small, wraplength=px(self, 460), justify="left", anchor="w").pack(
            fill="x", pady=(px(self, 10), 0))
        self._update_pp_info()

    def _update_pp_info(self) -> None:
        pp = self.pp
        dash = "–"

        def one(template: str, **kw) -> str:
            return " / ".join(pp.fill(template, **kw)) or dash

        rows = [
            ("Format", ARTEN.get(pp.art, pp.art)),
            ("Drehen", " / ".join(pp.tool_lines(1, 1, pp.vorschub_u, pp.ebene_drehen, 2300))),
            ("Positionieren", one(pp.c_pos, W=90)),
            ("Angetr. WZ", "{}  …  {}".format(one(pp.awz_ein, S=3000), one(pp.awz_aus))),
            ("C-Achse", "ein {}  ·  aus {}".format(one(pp.c_ein), one(pp.c_aus))),
            ("Klemmen", "{}  ·  lösen {}".format(one(pp.klemmen), one(pp.loesen))),
            ("Wechselpunkt", "X{} Z{}".format(pp.num(pp.wechsel_x), pp.num(pp.wechsel_z))),
        ]
        self.pp_info.configure(text="\n".join("{:<15}{}".format(a + ":", b) for a, b in rows))
        name = self.machine.display_name if self.machine is not None else "ohne Maschine"
        control = getattr(self.machine, "control", "")
        if control in ("heidenhain", "generic"):
            self.head_sub.configure(fg=C["warn"], text="Achtung: Maschine „{}“ – Code im Format {} laut "
                                    "Postprozessor.".format(name, ARTEN.get(pp.art, pp.art)))
        else:
            self.head_sub.configure(fg=C["muted"], text="Für {} ({}) · Element antippen · Drehen, Fräsen oder "
                                    "Entgraten wählen".format(name, ARTEN.get(pp.art, pp.art)))

    def select_machine(self, machine) -> None:
        self.machine = machine
        self.pp = fuer_maschine(machine)
        if hasattr(self, "machine_cb"):
            self.machine_cb.set(machine.display_name)
        self._update_pp_info()
        self.changed()

    def _machine_selected(self) -> None:
        names = [m.display_name for m in self.machines]
        name = self.machine_cb.get()
        if name in names:
            self.select_machine(self.machines[names.index(name)])

    def edit_postpro(self) -> None:
        from .postpro_dialog import PostproDialog

        name = self.machine.display_name if self.machine is not None else "Kontur-Assistent"
        res = PostproDialog(self, self.fonts, name, self.pp).show()
        if res is None:
            return
        self.set_postpro(res)

    def set_postpro(self, pp: Postprozessor) -> None:
        """Neuen Postprozessor übernehmen und bei der Maschine speichern."""
        self.pp = pp
        if self.machine is not None:
            self.machine.post = pp.to_dict()
            if self.on_machines_changed is not None:
                self.on_machines_changed()
        self._update_pp_info()
        self.changed()

    def _tab_code(self) -> None:
        f = self._tab("Code")
        top = tk.Frame(f, bg=C["surface"])
        top.pack(fill="x")
        ttk.Checkbutton(top, text="Werkzeugbahn in der Zeichnung zeigen", variable=self.show_path,
                        style="Surface.TCheckbutton", command=self.redraw).pack(side="left")
        self.code_info = tk.Label(f, text="", bg=C["surface"], fg=C["muted"], font=self.fonts.small, anchor="w",
                                  justify="left", wraplength=px(self, 460))
        self.code_info.pack(fill="x")
        box = tk.Frame(f, bg=C["surface"])
        box.pack(fill="both", expand=True, pady=(px(self, 6), 0))
        self.code_text = tk.Text(box, font=(MONO_FAMILY, 10), wrap="none", bg=C["surface_alt"], fg=C["text"],
                                 relief="flat", highlightthickness=1, highlightbackground=C["border"])
        sb = ttk.Scrollbar(box, orient="vertical", command=self.code_text.yview)
        self.code_text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")  # zuerst, sonst nimmt das Textfeld die ganze Breite
        self.code_text.configure(width=20)
        self.code_text.pack(side="left", fill="both", expand=True)
        self.code_text.configure(state="disabled")

    def _build_bottom(self) -> None:
        bar = tk.Frame(self, bg=C["bg"])
        bar.grid(row=2, column=0, sticky="ew", padx=px(self, 12), pady=px(self, 10))
        bar.grid_columnconfigure(0, weight=1)
        self.msg = tk.Label(bar, text="", bg=C["bg"], fg=C["text"], font=self.fonts.ui, anchor="w",
                            justify="left", wraplength=px(self, 640))
        self.msg.grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Abbrechen", command=self.cancel).grid(row=0, column=1, padx=(px(self, 8), 0))
        self.insert_btn = ttk.Button(bar, text="Am Cursor einfügen", command=lambda: self.finish("einfuegen"))
        self.insert_btn.grid(row=0, column=2, padx=(px(self, 8), 0))
        self.new_btn = ttk.Button(bar, text="Als neues Programm", style="Primary.TButton",
                                  command=lambda: self.finish("neu"))
        self.new_btn.grid(row=0, column=3, padx=(px(self, 8), 0))

    # ------------------------------------------------------------ Felder <-> Teil

    def _load_fields(self) -> None:
        self._loading = True
        t = self.teil
        for key, var in self.fvars.items():
            v = getattr(t, key)
            var.set(v if isinstance(v, str) else fmt(v))
        self._loading = False
        self._fill_tool_combos()

    def _field_changed(self, key: str, conv) -> None:
        if self._loading:
            return
        raw = self.fvars[key].get()
        try:
            if conv is str:
                value = raw.strip()
            elif conv is int:
                value = int(parse_num(raw))
            else:
                value = parse_num(raw)
        except ValueError:
            self._set_msg("„{}“ ist keine Zahl.".format(raw), "error")
            return
        if getattr(self.teil, key) != value:
            setattr(self.teil, key, value)
            self.changed()

    def _fill_tool_combos(self) -> None:
        t = self.teil
        rough = _tool_choices(t, ("schruppen", "schlichten"))
        self.cb_rough.configure(values=rough)
        self.cb_fine.configure(values=rough)
        for cb, nr in ((self.cb_rough, t.wz_schruppen), (self.cb_fine, t.wz_schlichten)):
            w = t.tool(nr)
            cb.set(w.label if w else "T{} ?".format(nr))

    def _turn_tools(self) -> None:
        for cb, attr in ((self.cb_rough, "wz_schruppen"), (self.cb_fine, "wz_schlichten")):
            nr = _tool_from_choice(cb.get())
            if nr is not None:
                setattr(self.teil, attr, nr)
        self.changed()

    # ---------------------------------------------------------------- Ablauf

    def changed(self) -> None:
        """Teil wurde geändert: neu rechnen, Listen und Zeichnung auffrischen (leicht verzögert)."""
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(120, self.refresh)

    def refresh(self) -> None:
        self._job = None
        self.geom = build_geometry(self.teil)
        self.res = generate(self.teil, rahmen=True, pp=self.pp, maschine=self._machine_name())
        self._fill_lists()
        self._update_selection()
        self.redraw()
        self._show_code()
        if self.res.errors:
            self._set_msg("✕  " + self.res.errors[0] + (
                "  (+{} weitere)".format(len(self.res.errors) - 1) if len(self.res.errors) > 1 else ""), "error")
        elif self.res.warnings:
            self._set_msg("!  " + self.res.warnings[0], "warn")
        else:
            self._set_msg("✓  {} Arbeitsgänge – Code und Werkzeugbahn im Reiter „Code“.".format(
                len(self.res.arbeitsgaenge)), "ok")
        for b in (self.insert_btn, self.new_btn):
            b.state(["disabled"] if self.res.errors else ["!disabled"])

    def _set_msg(self, text: str, level: str) -> None:
        color = {"error": C["error"], "warn": C["warn"], "ok": C["ok"]}.get(level, C["text"])
        self.msg.configure(text=text, fg=color)

    def _fill_lists(self) -> None:
        t = self.teil
        sel_pt = self.pt_tree.selection()
        self.pt_tree.delete(*self.pt_tree.get_children())
        for i, p in enumerate(t.punkte):
            op = "Start (Mitte)" if i == 0 else SEG_OPS.get(p.op, p.op)
            length = abs(p.z - t.punkte[i - 1].z) if i else 0.0
            self.pt_tree.insert("", "end", iid="p{}".format(i),
                                values=(i, fmt(p.x), fmt(p.z), fmt(length) if length > 1e-9 else "", op,
                                        _ecke_text(p, t) if 0 < i < len(t.punkte) - 1 else ""))
        if sel_pt and self.pt_tree.exists(sel_pt[0]):
            self.pt_tree.selection_set(sel_pt[0])
        sel_ft = self.ft_tree.selection()
        self.ft_tree.delete(*self.ft_tree.get_children())
        for i, fr in enumerate(t.fraesungen):
            self.ft_tree.insert("", "end", iid="f{}".format(i),
                                values=(i + 1, FEATURES[fr.art], _feature_text(fr), winkel_text(fr.winkel),
                                        "ja" if fr.entgraten else ""))
        if sel_ft and self.ft_tree.exists(sel_ft[0]):
            self.ft_tree.selection_set(sel_ft[0])
        self.wz_tree.delete(*self.wz_tree.get_children())
        for w in sorted(t.werkzeuge, key=lambda w: w.t):
            art = {"schruppen": "Schruppen", "schlichten": "Schlichten", "fraeser": "Fräser", "bohrer": "Bohrer",
                   "fase": "Fase"}.get(w.art, w.art)
            if w.lage:
                art += " " + {"radial": "rad.", "axial": "ax."}.get(w.lage, w.lage)
            kat = " · ".join(x for x in (w.halter, w.platte) if x) or "–"
            self.wz_tree.insert("", "end", iid="t{}".format(w.t),
                                values=(w.t, w.name, kat, art, fmt(w.s), fmt(w.f), fmt(w.ap) if w.ap else ""))
        self._fill_tool_combos()

    def _show_code(self) -> None:
        res = self.res
        self.code_text.configure(state="normal")
        self.code_text.delete("1.0", "end")
        if res.errors:
            self.code_text.insert("1.0", "Noch kein Code – bitte zuerst korrigieren:\n\n" +
                                  "\n".join("• " + e for e in res.errors))
            self.code_info.configure(text="")
        else:
            self.code_text.insert("1.0", res.code)
            n = res.code.count("\n")
            info = "Arbeitsgänge: " + ", ".join(res.arbeitsgaenge) + "\n{} Zeilen.  „Als neues Programm“ mit " \
                   "Programmanfang und -ende laut Postprozessor, „Am Cursor einfügen“ nur die Arbeitsgänge." \
                .format(n)
            if res.warnings:
                info += "\n" + "\n".join("! " + w for w in res.warnings)
            self.code_info.configure(text=info)
        self.code_text.configure(state="disabled")

    def finish(self, mode: str) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
            self.refresh()
        res = generate(self.teil, rahmen=(mode == "neu"), pp=self.pp, maschine=self._machine_name())
        if res.errors:
            dialogs.error(self, self.fonts, "Code kann noch nicht erzeugt werden", res.errors[0],
                          detail="\n".join(res.errors[1:4]))
            return
        self.result = (mode, res.code)
        self.destroy()

    def _machine_name(self) -> str:
        return self.machine.display_name if self.machine is not None else ""

    def new_part(self) -> None:
        if not dialogs.confirm(self, self.fonts, "Neues Teil", "Kontur und Fräsungen löschen? Werkzeuge und "
                               "Postprozessor bleiben.", yes="Neu"):
            return
        self._replace_part(neues_teil(), keep_tools=True)

    def load_example(self) -> None:
        if len(self.teil.punkte) > 1 and not dialogs.confirm(
                self, self.fonts, "Beispiel laden", "Das aktuelle Teil wird durch das Beispiel ersetzt.",
                yes="Beispiel laden"):
            return
        self._replace_part(beispiel_teil(), keep_tools=False)

    def _replace_part(self, new: Teil, keep_tools: bool) -> None:
        old = self.teil
        if keep_tools:
            for attr in ("werkzeuge", "wz_schruppen", "wz_schlichten", "aufmass_x", "aufmass_z", "kante",
                         "sicherheit"):
                setattr(new, attr, getattr(old, attr))
        self.teil.__dict__.update(new.__dict__)
        self.sel = None
        self._load_fields()
        self.refresh()

    # ------------------------------------------------------------- Konturpunkte

    def _selected_point(self) -> Optional[int]:
        sel = self.pt_tree.selection()
        return int(sel[0][1:]) if sel else None

    def add_step(self) -> None:
        last = self.teil.punkte[-1]
        res = FormDialog(self, self.fonts, "Absatz anhängen",
                         [("x", "Durchmesser Ø", fmt(last.x) if last.x else ""), ("l", "Länge", "")],
                         message="Vom letzten Punkt (X{} Z{}) senkrecht auf den Durchmesser, dann die Länge nach "
                                 "links.".format(fmt(last.x), fmt(last.z))).show()
        if not res:
            return
        try:
            x, ln = parse_num(res["x"]), parse_num(res["l"])
        except ValueError:
            dialogs.error(self, self.fonts, "Ungültige Eingabe", "Durchmesser und Länge als Zahl eingeben.")
            return
        self.append_step(x, ln)

    def append_step(self, x: float, laenge: float) -> None:
        last = self.teil.punkte[-1]
        if abs(x - last.x) > 1e-9:
            self.teil.punkte.append(KPunkt(x, last.z))
        if laenge > 0:
            self.teil.punkte.append(KPunkt(x, last.z - abs(laenge)))
        self.changed()

    def add_point(self) -> None:
        last = self.teil.punkte[-1]
        res = FormDialog(self, self.fonts, "Punkt einfügen",
                         [("x", "X (Durchmesser)", fmt(last.x)), ("z", "Z", fmt(last.z))],
                         message="Der Punkt kommt hinter den markierten Punkt bzw. ans Ende. Ein Kegel entsteht "
                                 "einfach durch X und Z zugleich.").show()
        if not res:
            return
        try:
            x, z = parse_num(res["x"]), parse_num(res["z"])
        except ValueError:
            dialogs.error(self, self.fonts, "Ungültige Eingabe", "X und Z als Zahl eingeben.")
            return
        self.insert_point(x, z)

    def insert_point(self, x: float, z: float, after: Optional[int] = None) -> None:
        if after is None:
            after = self._selected_point()
        pos = len(self.teil.punkte) if after is None else after + 1
        self.teil.punkte.insert(pos, KPunkt(x, z))
        self.changed()
        self.pt_tree.selection_set(())
        self.after_idle(lambda: self.pt_tree.exists("p{}".format(pos)) and
                        self.pt_tree.selection_set("p{}".format(pos)))

    def edit_point(self, i: Optional[int] = None) -> None:
        if i is None:
            i = self._selected_point()
        if i is None:
            dialogs.info(self, self.fonts, "Punkt wählen", "Bitte zuerst einen Punkt in der Liste antippen.")
            return
        if i == 0:
            dialogs.info(self, self.fonts, "Startpunkt", "Der erste Punkt ist immer X0 Z0 (Mitte der Stirnseite).")
            return
        p = self.teil.punkte[i]
        fields = [("x", "X (Durchmesser)", fmt(p.x)), ("z", "Z", fmt(p.z)),
                  ("op", "Element bis hier", SEG_OPS[p.op], list(SEG_OPS.values()))]
        if i < len(self.teil.punkte) - 1:
            fields += [("ecke", "Ecke", ECKEN[p.ecke], list(ECKEN.values())),
                       ("wert", "Fase / Radius (mm)", fmt(p.wert) if p.wert else "")]
        res = FormDialog(self, self.fonts, "Punkt {} ändern".format(i), fields).show()
        if not res:
            return
        try:
            p.x, p.z = parse_num(res["x"]), parse_num(res["z"])
            if "ecke" in res:
                p.ecke = _inv(ECKEN)[res["ecke"]]
                p.wert = parse_num(res["wert"]) if res["wert"] else 0.0
        except (ValueError, KeyError):
            dialogs.error(self, self.fonts, "Ungültige Eingabe", "Bitte Zahlen eintragen (Komma oder Punkt).")
            self.changed()
            return
        p.op = _inv(SEG_OPS).get(res["op"], p.op)
        self.changed()

    def delete_point(self) -> None:
        i = self._selected_point()
        if i is None or i == 0:
            dialogs.info(self, self.fonts, "Punkt wählen",
                         "Bitte einen Punkt (nicht den Startpunkt) in der Liste antippen.")
            return
        del self.teil.punkte[i]
        if self.sel and self.sel[0] in ("seg", "corner"):
            self.sel = None
        self.changed()

    def _pt_selected(self, _e=None) -> None:
        i = self._selected_point()
        if i is None or i == 0 or self.sel in (("corner", i), ("seg", i)):
            return  # z. B. Auswahl kam aus der Zeichnung
        self.sel = ("corner", i)
        self._update_selection()
        self.redraw()

    # ---------------------------------------------------------- Elemente/Ecken

    def set_segment_op(self, i: int, op: str) -> None:
        self.teil.punkte[i].op = op
        self.changed()

    def set_corner(self, i: int, ecke: str, wert: Optional[float] = None) -> None:
        p = self.teil.punkte[i]
        if ecke in ("fase", "radius") and wert is None:
            v = dialogs.ask_text(self, self.fonts, ECKEN[ecke], "{} an Punkt {} (mm):".format(ECKEN[ecke], i),
                                 fmt(p.wert) if p.ecke == ecke and p.wert else ("0.5" if ecke == "fase" else "1"))
            if v is None:
                return
            try:
                wert = parse_num(v)
            except ValueError:
                dialogs.error(self, self.fonts, "Ungültige Eingabe", "Bitte eine Zahl eintragen.")
                return
        p.ecke = ecke
        p.wert = wert or 0.0
        self.changed()

    # ---------------------------------------------------------------- Fräsungen

    def _default_feature(self, art: str, seg: Optional[int] = None) -> Fraesung:
        t = self.teil
        fr = Fraesung(art=art)
        if seg is not None:
            a, b = t.punkte[seg - 1], t.punkte[seg]
            fr.z1, fr.z2 = max(a.z, b.z), min(a.z, b.z)
            d = max(a.x, b.x)
        else:
            d = t.punkte[-1].x if len(t.punkte) > 1 else t.rohteil_d
            fr.z1, fr.z2 = 0.0, -min(10.0, abs(t.punkte[-1].z) or 10.0)
        if art == "flaeche":
            fr.mass = math.floor(d * 0.85) or 1.0
            fr.winkel = [0.0, 180.0]
        elif art == "nut":
            fr.mass, fr.tiefe = 6.0, 2.0
        elif art == "bohrung_radial":
            fr.z1 = (fr.z1 + fr.z2) / 2 if seg is not None else -5.0
            fr.mass, fr.tiefe = 5.0, round(d / 2, 1) or 5.0
            fr.winkel = [0.0]
        else:
            fr.z1 = 0.0
            fr.mass, fr.tiefe, fr.teilkreis = 5.0, 8.0, round(d * 0.6)
            fr.winkel = gleich_verteilt(4)
        arten = ("bohrer",) if art.startswith("bohrung") else ("fraeser",)
        lage = FEATURE_LAGE[art]
        for w in sorted(t.werkzeuge, key=lambda w: w.t):
            if w.art in arten and (not w.lage or w.lage == lage):
                fr.wz = w.t
                break
        for w in sorted(t.werkzeuge, key=lambda w: w.t):
            if w.art == "fase" and (not w.lage or w.lage == lage):
                fr.wz_entgraten = w.t
                break
        return fr

    def add_feature(self, art: str, seg: Optional[int] = None) -> None:
        fr = self._default_feature(art, seg)
        res = FeatureDialog(self, self.fonts, self.teil, fr, FEATURES[art] + " hinzufügen").show()
        if res is None:
            return
        self.teil.fraesungen.append(res)
        self.sel = ("feat", len(self.teil.fraesungen) - 1)
        self.changed()

    def _selected_feature(self) -> Optional[int]:
        if self.sel and self.sel[0] == "feat":
            return self.sel[1]
        sel = self.ft_tree.selection()
        return int(sel[0][1:]) if sel else None

    def edit_feature(self, j: Optional[int] = None) -> None:
        if j is None:
            j = self._selected_feature()
        if j is None or j >= len(self.teil.fraesungen):
            dialogs.info(self, self.fonts, "Fräsung wählen", "Bitte zuerst eine Fräsung antippen.")
            return
        fr = self.teil.fraesungen[j]
        res = FeatureDialog(self, self.fonts, self.teil, fr, "Fräsung {} – {}".format(j + 1, FEATURES[fr.art])).show()
        if res is not None:
            self.teil.fraesungen[j] = res
            self.changed()

    def toggle_deburr(self, j: Optional[int] = None) -> None:
        if j is None:
            j = self._selected_feature()
        if j is None or j >= len(self.teil.fraesungen):
            return
        fr = self.teil.fraesungen[j]
        fr.entgraten = not fr.entgraten
        if fr.entgraten and not self.teil.tool(fr.wz_entgraten):
            fr.wz_entgraten = self._default_feature(fr.art).wz_entgraten
        self.changed()

    def delete_feature(self, j: Optional[int] = None) -> None:
        if j is None:
            j = self._selected_feature()
        if j is None or j >= len(self.teil.fraesungen):
            return
        del self.teil.fraesungen[j]
        self.sel = None
        self.changed()

    def _ft_selected(self, _e=None) -> None:
        sel = self.ft_tree.selection()
        if sel:
            j = int(sel[0][1:])
            if self.sel != ("feat", j):
                self.sel = ("feat", j)
                self._update_selection()
                self.redraw()

    # ---------------------------------------------------------------- Werkzeuge

    def _tool_form(self, w: Werkzeug, title: str) -> Optional[Werkzeug]:
        from .werkzeug_dialog import WerkzeugDialog

        return WerkzeugDialog(self, self.fonts, w, title).show()

    def add_tool(self) -> None:
        nr = max([w.t for w in self.teil.werkzeuge] + [0]) + 1
        new = self._tool_form(Werkzeug(nr, 1, "", "schlichten", "", 0, 2500, 0.1, 0), "Werkzeug hinzufügen")
        if new is None:
            return
        if self.teil.tool(new.t):
            dialogs.error(self, self.fonts, "T{} gibt es schon".format(new.t), "Bitte eine andere Nummer wählen.")
            return
        self.teil.werkzeuge.append(new)
        self.changed()

    def _selected_tool(self) -> Optional[Werkzeug]:
        sel = self.wz_tree.selection()
        return self.teil.tool(int(sel[0][1:])) if sel else None

    def edit_tool(self) -> None:
        w = self._selected_tool()
        if w is None:
            dialogs.info(self, self.fonts, "Werkzeug wählen", "Bitte zuerst ein Werkzeug in der Liste antippen.")
            return
        new = self._tool_form(w, "T{} ändern".format(w.t))
        if new is None:
            return
        if new.t != w.t and self.teil.tool(new.t):
            dialogs.error(self, self.fonts, "T{} gibt es schon".format(new.t), "Bitte eine andere Nummer wählen.")
            return
        self.teil.werkzeuge[self.teil.werkzeuge.index(w)] = new
        if new.t != w.t:  # Verweise mitnehmen
            t = self.teil
            if t.wz_schruppen == w.t:
                t.wz_schruppen = new.t
            if t.wz_schlichten == w.t:
                t.wz_schlichten = new.t
            for fr in t.fraesungen:
                if fr.wz == w.t:
                    fr.wz = new.t
                if fr.wz_entgraten == w.t:
                    fr.wz_entgraten = new.t
        self.changed()

    def delete_tool(self) -> None:
        w = self._selected_tool()
        if w is None:
            return
        self.teil.werkzeuge.remove(w)
        self.changed()

    # ------------------------------------------------------------ Auswahlleiste

    def select(self, kind: Optional[str], nr: int = 0) -> None:
        self.sel = (kind, nr) if kind else None
        if kind == "corner" and self.pt_tree.exists("p{}".format(nr)):
            self.pt_tree.selection_set("p{}".format(nr))
        elif kind == "seg" and self.pt_tree.exists("p{}".format(nr)):
            self.pt_tree.selection_set("p{}".format(nr))
        elif kind == "feat" and self.ft_tree.exists("f{}".format(nr)):
            self.ft_tree.selection_set("f{}".format(nr))
        self._update_selection()
        self.redraw()

    def _bar_button(self, text: str, cmd, active: bool = False) -> None:
        b = ttk.Button(self.sel_buttons, text=text, command=cmd, width=0,
                       style="BarPrimary.TButton" if active else "Bar.TButton")
        b.pack(side="left", padx=(0, px(self, 6)))

    def _bar_label(self, text: str) -> None:
        tk.Label(self.sel_buttons, text=text, bg=C["surface"], fg=C["muted"], font=self.fonts.small).pack(
            side="left", padx=(px(self, 8), px(self, 4)))

    def _update_selection(self) -> None:
        for w in self.sel_buttons.winfo_children():
            w.destroy()
        t = self.teil
        sel = self.sel
        n = len(t.punkte) - 1
        if sel and sel[0] in ("seg", "corner") and not 0 < sel[1] <= n:
            sel = self.sel = None
        if sel and sel[0] == "feat" and sel[1] >= len(t.fraesungen):
            sel = self.sel = None
        if not sel:
            self.sel_label.configure(text="Element, Ecke (Punkt) oder Fräsung in der Zeichnung antippen.")
            self._bar_button("+ Absatz …", self.add_step)
            self._bar_button("+ Fläche", lambda: self.add_feature("flaeche"))
            self._bar_button("+ Nut", lambda: self.add_feature("nut"))
            self._bar_button("+ Bohrung", lambda: self.add_feature("bohrung_radial"))
            return
        kind, i = sel
        if kind == "seg":
            a, b = t.punkte[i - 1], t.punkte[i]
            if abs(a.z - b.z) < 1e-9:
                form = "Planfläche" if min(a.x, b.x) < 1e-9 else "Absatz"
            elif abs(a.x - b.x) < 1e-9:
                form = "Zylinder Ø{}".format(fmt(a.x))
            else:
                form = "Kegel Ø{} → Ø{}".format(fmt(a.x), fmt(b.x))
            self.sel_label.configure(text="Element {}:  {}   (X{} Z{} → X{} Z{})".format(
                i, form, fmt(a.x), fmt(a.z), fmt(b.x), fmt(b.z)))
            self._bar_button("Drehen", lambda: self.set_segment_op(i, "drehen"), b.op != "frei")
            self._bar_button("Nicht drehen", lambda: self.set_segment_op(i, "frei"), b.op == "frei")
            self._bar_label("Fräsen hier:")
            if abs(a.z - b.z) < 1e-9:
                self._bar_button("Stirnbohrung", lambda: self.add_feature("bohrung_axial", i))
            else:
                self._bar_button("Fläche", lambda: self.add_feature("flaeche", i))
                self._bar_button("Nut", lambda: self.add_feature("nut", i))
                self._bar_button("Bohrung", lambda: self.add_feature("bohrung_radial", i))
        elif kind == "corner":
            p = t.punkte[i]
            self.sel_label.configure(text="Punkt {}:  X{} Z{}{}".format(
                i, fmt(p.x), fmt(p.z), "   ·   Kante: " + (_ecke_text(p, t) or "scharf") if i < n else
                "   ·   Konturende"))
            if i < n:
                self._bar_button("Scharf", lambda: self.set_corner(i, ""), p.ecke == "")
                self._bar_button("Entgraten", lambda: self.set_corner(i, "entgraten"), p.ecke == "entgraten")
                self._bar_button("Fase …", lambda: self.set_corner(i, "fase"), p.ecke == "fase")
                self._bar_button("Radius …", lambda: self.set_corner(i, "radius"), p.ecke == "radius")
            self._bar_button("Punkt ändern …", lambda: self.edit_point(i))
        else:
            fr = t.fraesungen[i]
            self.sel_label.configure(text="Fräsung {}:  {}  ·  {}  ·  C {}°".format(
                i + 1, FEATURES[fr.art], _feature_text(fr), winkel_text(fr.winkel).replace(" ", " / ")))
            self._bar_button("Ändern …", lambda: self.edit_feature(i))
            self._bar_button("Entgraten", lambda: self.toggle_deburr(i), fr.entgraten)
            self._bar_button("Löschen", lambda: self.delete_feature(i))

    # ---------------------------------------------------------------- Zeichnung

    def _click(self, event) -> None:
        """Antippen: das dem Finger nächste Element. Eckpunkte sind klein und bekommen einen Bonus,
        Fräsungen einen kleineren – so trifft man eine Ecke auf der Linie, aber eine Fräsung, in die man
        hineintippt, auch wenn eine Ecke in der Nähe liegt."""
        r = px(self, 14)
        bonus = {"corner": px(self, 10), "feat": px(self, 3), "seg": 0}
        items = self.cv.find_overlapping(event.x - r, event.y - r, event.x + r, event.y + r)
        best: Optional[Tuple[float, str, int]] = None
        for item in items:
            d = self._distance(item, event.x, event.y)
            for tag in self.cv.gettags(item):
                for kind in ("corner", "feat", "seg"):
                    if tag.startswith(kind) and tag[len(kind):].isdigit():
                        score = d - bonus[kind]
                        if best is None or score < best[0]:
                            best = (score, kind, int(tag[len(kind):]))
        if best is None:
            self.select(None)
        else:
            self.select(best[1], best[2])

    def _distance(self, item, x: float, y: float) -> float:
        """Abstand eines Canvas-Elements zum Tippunkt (Linie: zur Strecke, sonst zur Fläche)."""
        cv = self.cv
        kind = cv.type(item)
        if kind == "line":
            c = cv.coords(item)
            best = float("inf")
            for i in range(0, len(c) - 3, 2):
                ax, ay, bx, by = c[i:i + 4]
                dx, dy = bx - ax, by - ay
                ln = dx * dx + dy * dy
                t = 0.0 if ln == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ln))
                best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
            return best
        bb = cv.bbox(item)
        if not bb:
            return float("inf")
        if kind == "oval":  # Eckpunkt: Abstand zur Mitte
            return math.hypot(x - (bb[0] + bb[2]) / 2, y - (bb[1] + bb[3]) / 2)
        dx = max(bb[0] - x, 0, x - bb[2])
        dy = max(bb[1] - y, 0, y - bb[3])
        return math.hypot(dx, dy)

    def redraw(self) -> None:
        cv = self.cv
        cv.delete("all")
        W, H = cv.winfo_width(), cv.winfo_height()
        if W < 50 or H < 50:
            return
        t = self.teil
        R = max(1.0, t.rohteil_d / 2)
        dims = masse(t)
        stock_end = dims["rohteil"][1] if dims["rohteil"] else None
        z_last = min([p.z for p in t.punkte] + [min(f.z1, f.z2) for f in t.fraesungen if f.art in ("flaeche", "nut")]
                     + [f.z1 for f in t.fraesungen if f.art == "bohrung_radial"]
                     + ([stock_end] if stock_end is not None else []))
        z_lo = z_last - (max(2.0, 0.05 * max(10.0, -z_last)) if stock_end is not None and stock_end <= z_last + 1e-6
                         else max(4.0, 0.12 * max(10.0, -z_last)))
        z_hi = max(t.planaufmass, 0) + max(3.0, 0.08 * max(10.0, -z_last))
        end_w = max(px(self, 150), min(W * 0.27, H * 0.7))
        side_w = W - end_w
        m = px(self, 26)
        path = self._path_visible()
        rows = [r for r in ("kette", "gesamt", "rohteil") if dims[r]]
        row_h = px(self, 24)
        bottom = px(self, 24) + (px(self, 18) if path else 0) + len(rows) * row_h + (px(self, 12) if rows else 0)
        top = px(self, 34)
        s = min((side_w - 2 * m) / (z_hi - z_lo), max(20.0, H - top - bottom) / (2.1 * R))
        s = max(s, 0.1)
        cx0 = m + (side_w - 2 * m - (z_hi - z_lo) * s) / 2
        cy = top + 1.05 * R * s

        def P(z: float, r: float) -> Tuple[float, float]:
            return cx0 + (z - z_lo) * s, cy - r * s

        self._P = P
        f = self.fonts
        cv.create_text(px(self, 10), px(self, 8), text="Seitenansicht", anchor="nw", fill=C["faint"],
                       font=f.small_bold)
        # Mittellinie, Rohteil
        cv.create_line(*P(z_lo - 2, 0), *P(z_hi, 0), fill=C["faint"], dash=(12, 4, 2, 4))
        x0, y0 = P(max(0.0, t.planaufmass), R)
        x1, y1 = P(stock_end if stock_end is not None else z_lo, -R)
        if stock_end is not None:
            cv.create_rectangle(x0, y0, x1, y1, outline=C["faint"], dash=(4, 3))
        else:  # Stange: offen, mit Bruchlinie
            cv.create_line(x1, y0, x0, y0, x0, y1, x1, y1, fill=C["faint"], dash=(4, 3))
            zz = []
            for i in range(7):
                zz += [x1 + (px(self, 4) if i % 2 else -px(self, 4)), y0 + (y1 - y0) * i / 6]
            cv.create_line(*zz, fill=C["faint"])
            cv.create_text(x1 + px(self, 8), y1 - px(self, 4), text="Stange", anchor="sw", fill=C["faint"],
                           font=self.fonts.small)
        geom = self.geom
        pl = polyline(geom.elems, 6.0) if geom.elems else [(p.z, p.x / 2) for p in t.punkte]
        if len(pl) >= 2:
            pts: List[float] = []
            for z, r in pl + [(pl[-1][0], -pl[-1][1])] + [(z, -r) for z, r in reversed(pl)]:
                pts.extend(P(z, r))
            cv.create_polygon(*pts, fill="#E3ECF8", outline="")
            low: List[float] = []
            for z, r in pl:
                low.extend(P(z, -r))
            cv.create_line(*low, fill="#9FB3CC", width=2)
            cv.create_line(*P(pl[-1][0], pl[-1][1]), *P(pl[-1][0], -pl[-1][1]), fill="#9FB3CC", width=1)
        sel = self.sel
        # Konturelemente
        if geom.errors:
            raw: List[float] = []
            for p in t.punkte:
                raw.extend(P(p.z, p.x / 2))
            if len(raw) >= 4:
                cv.create_line(*raw, fill=C["error"], width=3)
        for e in geom.elems:
            if e.seg >= 0:
                turned = t.punkte[e.seg].op != "frei"
                tag = "seg{}".format(e.seg)
                selected = sel == ("seg", e.seg)
            else:
                turned = t.punkte[e.corner].op != "frei" and t.punkte[e.corner + 1].op != "frei"
                tag = "corner{}".format(e.corner)
                selected = sel == ("corner", e.corner)
            xy: List[float] = []
            for z, r in [(e.z0, e.r0)] + e.points(6.0):
                xy.extend(P(z, r))
            color = SEL_COLOR if selected else (C["primary"] if turned else C["faint"])
            cv.create_line(*xy, fill=color, width=px(self, 7 if selected else 4), capstyle="round", tags=(tag,))
            cv.create_line(*xy, fill="", width=px(self, 22), tags=(tag,))  # größere Trefferfläche
            if e.seg >= 0 and abs(e.r1 - e.r0) < 1e-9 and abs(e.z1 - e.z0) * s > px(self, 34):
                mx, my = P((e.z0 + e.z1) / 2, e.r0)
                cv.create_text(mx, my - px(self, 12), text="Ø" + fmt(2 * e.r0), fill=C["muted"], font=f.small)
        # Fräsungen
        for j, fr in enumerate(t.fraesungen):
            self._draw_feature_side(j, fr, P, s, sel == ("feat", j))
        # Längenmaße unter dem Teil
        self._draw_dims(dims, rows, P, cy + R * s + px(self, 20), row_h, R)
        # Werkzeugbahn (Drehen) – nur im Reiter „Code“
        if path:
            for mv in self.res.moves:
                pts = mv.points()
                for (za, ra), (zb, rb) in zip(pts, pts[1:]):
                    seg = _clip(P(za, ra), P(zb, rb), side_w)
                    if seg is None:
                        continue
                    if mv.kind == "G0":
                        cv.create_line(*seg, fill=PATH_G0, width=1, dash=(3, 3))
                    else:
                        cv.create_line(*seg, fill=PATH_G1, width=1)
        # Punkte / Ecken
        n = len(t.punkte) - 1
        for i, p in enumerate(t.punkte):
            if i == 0:
                continue
            x, y = P(p.z, p.x / 2)
            rr = px(self, 9 if sel == ("corner", i) else 7)
            if sel == ("corner", i):
                fill = SEL_COLOR
            elif p.ecke == "entgraten" and i < n:
                fill = C["warn"]
            elif p.ecke in ("fase", "radius") and i < n:
                fill = C["primary"]
            else:
                fill = C["surface"]
            cv.create_oval(x - rr, y - rr, x + rr, y + rr, fill=fill, outline=C["text"], width=1,
                           tags=("corner{}".format(i),))
            cv.create_text(x + rr + 2, y - rr - 2, text=str(i), anchor="sw", fill=C["muted"], font=f.small,
                           tags=("corner{}".format(i),))
        # Legende
        lx, ly = px(self, 10), H - px(self, 10)
        for color, text in ((C["primary"], "drehen"), (C["faint"], "nicht drehen"), (C["warn"], "entgraten"),
                            (FEAT_COLOR, "fräsen/bohren")):
            cv.create_rectangle(lx, ly - px(self, 9), lx + px(self, 14), ly - px(self, 3), fill=color, outline="")
            item = cv.create_text(lx + px(self, 18), ly, text=text, anchor="sw", fill=C["muted"], font=f.small)
            lx = cv.bbox(item)[2] + px(self, 14)
        if path:
            lx, ly = px(self, 10), ly - px(self, 18)
            cv.create_line(lx, ly - px(self, 6), lx + px(self, 16), ly - px(self, 6), fill=PATH_G0, dash=(3, 3))
            item = cv.create_text(lx + px(self, 20), ly, text="Eilgang", anchor="sw", fill=C["muted"], font=f.small)
            lx = cv.bbox(item)[2] + px(self, 10)
            cv.create_line(lx, ly - px(self, 6), lx + px(self, 16), ly - px(self, 6), fill=PATH_G1)
            cv.create_text(lx + px(self, 20), ly, text="Vorschub", anchor="sw", fill=C["muted"], font=f.small)
        self._draw_end_view(side_w, W, H)

    def _draw_dims(self, dims, rows, P, y0: float, row_h: float, R: float) -> None:
        """Maßketten: Abschnitte der Kontur, Gesamtlänge, Rohteillänge (mit Maßhilfslinien)."""
        cv = self.cv
        t = self.teil

        def r_at(z: float) -> float:
            rs = [p.x / 2 for p in t.punkte if abs(p.z - z) < 1e-6]
            return max(rs) if rs else R

        arrow = (px(self, 7), px(self, 8), px(self, 3))
        for k, row in enumerate(rows):
            y = y0 + k * row_h
            items = dims[row] if row == "kette" else [dims[row]]
            last_left = None
            for za, zb, length in items:
                xa, xb = P(za, 0)[0], P(zb, 0)[0]
                for x, z in ((xa, za), (xb, zb)):
                    r = R if row == "rohteil" else r_at(z)
                    cv.create_line(x, P(z, -r)[1] + px(self, 3), x, y + px(self, 5), fill=C["faint"])
                cv.create_line(xb, y, xa, y, fill=C["muted"], arrow="both", arrowshape=arrow)
                text = fmt(length) if row != "rohteil" else "Rohteil " + fmt(length)
                ty = y - px(self, 1)
                item = cv.create_text((xa + xb) / 2, ty, text=text, anchor="s", fill=C["text"],
                                      font=self.fonts.small)
                x0, _, x1, _ = cv.bbox(item)
                if last_left is not None and x1 > last_left - px(self, 3):
                    cv.move(item, 0, -px(self, 11))  # zu eng: Zahl etwas höher setzen
                    last_left = None
                else:
                    last_left = x0
                bb = cv.bbox(item)
                back = cv.create_rectangle(bb[0] - 1, bb[1], bb[2] + 1, bb[3] - 1, fill=C["surface"], outline="")
                cv.tag_lower(back, item)

    def _path_visible(self) -> bool:
        try:
            on_code_tab = self.nb.index("current") == 4
        except tk.TclError:
            on_code_tab = False
        return bool(on_code_tab and self.show_path.get() and self.res.ok and self.res.moves)

    def _draw_feature_side(self, j: int, fr: Fraesung, P, s: float, selected: bool) -> None:
        cv = self.cv
        t = self.teil
        tag = "feat{}".format(j)
        outline = SEL_COLOR if selected else FEAT_COLOR
        width = px(self, 3 if selected else 2)
        if fr.art in ("flaeche", "nut"):
            z_hi, z_lo = min(0.0, max(fr.z1, fr.z2)), min(fr.z1, fr.z2)
            rp = contour_radius(t, self.geom, z_lo, z_hi) if self.geom.elems else t.rohteil_d / 2
            bottom = fr.mass / 2 if fr.art == "flaeche" else rp - fr.tiefe
            a, b = P(z_lo, rp), P(z_hi, bottom)
            label = "SW{}".format(fmt(fr.mass)) if fr.art == "flaeche" else "Nut {}".format(fmt(fr.mass))
        elif fr.art == "bohrung_radial":
            rp = contour_radius(t, self.geom, fr.z1 - fr.mass / 2, fr.z1 + fr.mass / 2) if self.geom.elems \
                else t.rohteil_d / 2
            a, b = P(fr.z1 - fr.mass / 2, rp), P(fr.z1 + fr.mass / 2, rp - fr.tiefe)
            label = "Ø{}".format(fmt(fr.mass))
        else:
            rc = fr.teilkreis / 2
            a, b = P(fr.z1 - fr.tiefe, rc + fr.mass / 2), P(fr.z1, rc - fr.mass / 2)
            label = "Ø{}".format(fmt(fr.mass))
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        cv.create_rectangle(x0, y0, x1, max(y1, y0 + 2), fill=FEAT_COLOR, stipple="gray50", outline=outline,
                            width=width, tags=(tag,))
        cv.create_text((x0 + x1) / 2, max(y1, y0 + 2) + px(self, 3), anchor="n", fill=outline,
                       font=self.fonts.small, tags=(tag,),
                       text="{} · {}°".format(label, winkel_text(fr.winkel).replace(" ", "/")))

    def _draw_end_view(self, x0: float, W: float, H: float) -> None:
        cv = self.cv
        t = self.teil
        f = self.fonts
        cv.create_rectangle(x0, 1, W, H, fill=C["surface"], outline="")
        cv.create_line(x0, px(self, 8), x0, H - px(self, 8), fill=C["border"])
        cv.create_text(x0 + px(self, 10), px(self, 8), text="Stirnansicht · 0° oben", anchor="nw",
                       fill=C["faint"], font=f.small_bold)
        R = max(1.0, t.rohteil_d / 2)
        cx = (x0 + W) / 2
        cy = H / 2
        rad = min((W - x0) / 2 - px(self, 30), H / 2 - px(self, 40))
        if rad < 20:
            return
        s = rad / R
        cv.create_oval(cx - rad, cy - rad, cx + rad, cy + rad, outline=C["faint"], dash=(4, 3))
        rmax = max([p.x / 2 for p in t.punkte] + [0.1])
        cv.create_oval(cx - rmax * s, cy - rmax * s, cx + rmax * s, cy + rmax * s, outline=C["primary"], width=2,
                       fill="#E3ECF8")
        cv.create_line(cx - rad - 6, cy, cx + rad + 6, cy, fill=C["faint"], dash=(8, 3, 2, 3))
        cv.create_line(cx, cy - rad - 6, cx, cy + rad + 6, fill=C["faint"], dash=(8, 3, 2, 3))

        def U(a: float) -> Tuple[float, float]:
            ra = math.radians(a)
            return -math.sin(ra), -math.cos(ra)

        def Q(a: float, along: float, across: float) -> Tuple[float, float]:
            ux, uy = U(a)
            vx, vy = -uy, ux
            return cx + (ux * along + vx * across) * s, cy + (uy * along + vy * across) * s

        for j, fr in enumerate(t.fraesungen):
            tag = "feat{}".format(j)
            color = SEL_COLOR if self.sel == ("feat", j) else FEAT_COLOR
            if fr.art in ("flaeche", "nut"):
                z_hi, z_lo = min(0.0, max(fr.z1, fr.z2)), min(fr.z1, fr.z2)
                rp = contour_radius(t, self.geom, z_lo, z_hi) if self.geom.elems else R
            elif fr.art == "bohrung_radial":
                rp = contour_radius(t, self.geom, fr.z1 - fr.mass / 2, fr.z1 + fr.mass / 2) if self.geom.elems \
                    else R
            else:
                rp = R
            if fr.art != "bohrung_axial" and rp < R - 1e-6:
                cv.create_oval(cx - rp * s, cy - rp * s, cx + rp * s, cy + rp * s, outline=FEAT_COLOR, dash=(2, 3))
            for a in fr.winkel:
                if fr.art == "flaeche":
                    h = fr.mass / 2
                    w2 = math.sqrt(max(0.0, rp * rp - h * h))
                    poly = [Q(a, h, -w2), Q(a, h, w2), Q(a, rp, w2), Q(a, rp, -w2)]
                    cv.create_polygon(*[c for p in poly for c in p], fill=FEAT_COLOR, stipple="gray50", outline="",
                                      tags=(tag,))
                    cv.create_line(*Q(a, h, -w2), *Q(a, h, w2), fill=color, width=px(self, 3), tags=(tag,))
                elif fr.art == "nut":
                    b2 = fr.mass / 2
                    poly = [Q(a, rp - fr.tiefe, -b2), Q(a, rp - fr.tiefe, b2), Q(a, rp + 0.5, b2),
                            Q(a, rp + 0.5, -b2)]
                    cv.create_polygon(*[c for p in poly for c in p], fill=FEAT_COLOR, stipple="gray50",
                                      outline=color, width=px(self, 2), tags=(tag,))
                elif fr.art == "bohrung_radial":
                    cv.create_line(*Q(a, rp, 0), *Q(a, rp - fr.tiefe, 0), fill=color,
                                   width=max(px(self, 3), fr.mass * s), tags=(tag,))
                else:
                    hx, hy = Q(a, fr.teilkreis / 2, 0)
                    rr = max(px(self, 3), fr.mass / 2 * s)
                    cv.create_oval(hx - rr, hy - rr, hx + rr, hy + rr, outline=color, width=px(self, 2),
                                   fill=FEAT_COLOR, stipple="gray50", tags=(tag,))
                lx, ly = Q(a, R + px(self, 14) / s, 0)
                cv.create_text(lx, ly, text="{}°".format(fmt(a)), fill=color, font=f.small, tags=(tag,))
