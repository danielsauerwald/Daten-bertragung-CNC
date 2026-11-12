"""Fenster „Werkzeug“: Halter und Platte bzw. Bohrer/Fräser aus dem Katalog wählen."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from .dialogs import Dialog
from .kontur import LAGEN, TOOL_ARTEN, Werkzeug, kurzname, parse_num
from .postpro import fmt
from .system import IS_WIN, toggle_touch_keyboard
from .theme import C, Fonts, px
from . import werkzeugkatalog as wk

EIGENES = "(ohne Katalog)"
DREHEN = ("schruppen", "schlichten", "innen")


def _inv(d: Dict[str, str]) -> Dict[str, str]:
    return {v: k for k, v in d.items()}


def halter_auswahl(art: str) -> List[str]:
    """Katalog-Einträge, die zur Werkzeugart passen."""
    if art == "innen":  # Bohrstangen nach kleinster Bohrung
        bars = [h for h in wk.HALTER if h.art == "innen"]
        bars.sort(key=lambda h: (h.dmin, h.form, h.code))
        return [h.code for h in bars]
    if art in DREHEN:
        aussen = [h for h in wk.HALTER if h.art == "aussen"]
        aussen.sort(key=lambda h: (h.hand != "R", h.form, h.code))
        return [h.code for h in aussen]
    return [w.code for w in wk.RUNDWERKZEUGE if w.art == art]


def lagen_auswahl(art: str) -> List[str]:
    """Mögliche Lagen: Drehwerkzeuge keine, Bohrer auch stehend in Drehmitte."""
    if art in DREHEN:
        return [LAGEN[""]]
    keys = ["radial", "axial"] + (["mitte"] if art == "bohrer" else [])
    return [LAGEN[k] for k in keys]


class WerkzeugDialog(Dialog):
    """Ergebnis: Werkzeug oder None."""

    def __init__(self, parent, fonts: Fonts, w: Werkzeug, title: str) -> None:
        super().__init__(parent, fonts, title)
        self.resizable(False, False)
        self.w = w
        self._auto_name = self._suggest_name(w.halter, w.platte, w.art)
        bg = C["surface"]
        body = tk.Frame(self, bg=bg, padx=px(self, 22), pady=px(self, 14))
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(1, weight=1)
        self.body = body
        tk.Label(body, text=title, bg=bg, fg=C["text"], font=fonts.title, anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, px(self, 8)))
        self.v: Dict[str, tk.StringVar] = {}
        self._row = 1
        self._pair(("t", "T-Nummer", str(w.t), 6), ("d", "D", str(w.d), 4))
        self.art_cb = self._combo("art", "Art", TOOL_ARTEN[w.art], list(TOOL_ARTEN.values()))
        self.art_cb.bind("<<ComboboxSelected>>", lambda e: self._art_changed())
        self.lage_cb = self._combo("lage", "Lage", LAGEN.get(w.lage, LAGEN[""]), list(LAGEN.values()))
        self.lage_cb.bind("<<ComboboxSelected>>", lambda e: self._platte_changed())
        self.halter_lbl, self.halter_cb = self._combo("halter", "Halter", w.halter or EIGENES, [], with_label=True)
        self.halter_cb.bind("<<ComboboxSelected>>", lambda e: self._halter_changed())
        self.platte_lbl, self.platte_cb = self._combo("platte", "Platte", w.platte or EIGENES, [], with_label=True)
        self.platte_cb.bind("<<ComboboxSelected>>", lambda e: self._platte_changed())
        self._entry("name", "Bezeichnung", w.name, 24)
        self._pair(("dm", "Durchmesser", fmt(w.dm) if w.dm else "", 6), ("s", "Drehzahl S", fmt(w.s), 7))
        self._pair(("f", "Vorschub F", fmt(w.f), 6), ("ap", "Zustellung", fmt(w.ap) if w.ap else "", 7))
        self.info = tk.Label(body, text="", bg=bg, fg=C["muted"], font=fonts.small, anchor="w", justify="left",
                             wraplength=px(self, 560))
        self.info.grid(row=self._row, column=0, columnspan=4, sticky="w", pady=(px(self, 8), 0))
        self._row += 1
        self.msg = tk.Label(body, text="", bg=bg, fg=C["error"], font=fonts.ui, anchor="w", justify="left",
                            wraplength=px(self, 560))
        self.msg.grid(row=self._row, column=0, columnspan=4, sticky="w")
        bar = tk.Frame(self, bg=C["bg"])
        bar.pack(fill="x")
        ttk.Button(bar, text="Übernehmen", style="Primary.TButton", command=self.ok).pack(
            side="right", padx=(0, px(self, 12)), pady=px(self, 12))
        ttk.Button(bar, text="Abbrechen", command=self.cancel).pack(side="right", padx=(0, px(self, 12)))
        if IS_WIN:
            ttk.Button(bar, text="⌨", style="Small.TButton", width=3, command=toggle_touch_keyboard).pack(
                side="left", padx=px(self, 12))
        self.bind("<Return>", lambda e: self.ok())
        self._art_changed(initial=True)

    # -- Formular -------------------------------------------------------------

    def _label(self, text: str, col: int = 0) -> tk.Label:
        lab = tk.Label(self.body, text=text, bg=C["surface"], fg=C["text"], font=self.fonts.ui, anchor="w")
        lab.grid(row=self._row, column=col, sticky="w", padx=(0 if col == 0 else px(self, 14), px(self, 10)),
                 pady=px(self, 4))
        return lab

    def _entry(self, key: str, label: str, value: str, width: int, col: int = 0, newrow: bool = True):
        self._label(label, col)
        var = tk.StringVar(master=self, value=value)
        self.v[key] = var
        e = ttk.Entry(self.body, textvariable=var, width=width, font=self.fonts.ui)
        e.grid(row=self._row, column=col + 1, columnspan=3 if col == 0 and width > 10 else 1, sticky="w",
               pady=px(self, 4))
        if newrow:
            self._row += 1
        return e

    def _pair(self, a, b) -> None:
        self._entry(a[0], a[1], a[2], a[3], newrow=False)
        self._entry(b[0], b[1], b[2], b[3], col=2)

    def _combo(self, key: str, label: str, value: str, values: List[str], with_label: bool = False):
        lab = self._label(label)
        var = tk.StringVar(master=self, value=value)
        self.v[key] = var
        cb = ttk.Combobox(self.body, textvariable=var, values=values, state="readonly", width=30,
                          font=self.fonts.ui)
        cb.grid(row=self._row, column=1, columnspan=3, sticky="w", pady=px(self, 4))
        self._row += 1
        return (lab, cb) if with_label else cb

    # -- Abhängigkeiten -------------------------------------------------------

    def _art(self) -> str:
        return _inv(TOOL_ARTEN).get(self.v["art"].get(), "schruppen")

    def _art_changed(self, initial: bool = False) -> None:
        art = self._art()
        turning = art in DREHEN
        lagen = lagen_auswahl(art)
        self.lage_cb.configure(state="disabled" if turning else "readonly", values=lagen)
        if turning:
            self.v["lage"].set(LAGEN[""])
        elif self.v["lage"].get() not in lagen:
            self.v["lage"].set(LAGEN["radial"])
        choices = halter_auswahl(art)
        self.halter_lbl.configure(text={"innen": "Bohrstange"}.get(art, "Halter") if turning
                                  else "Werkzeug (Katalog)")
        self.halter_cb.configure(values=[EIGENES] + choices)
        if self.v["halter"].get() not in choices:
            self.v["halter"].set(EIGENES)
        if turning:
            self.platte_cb.grid()
            self.platte_lbl.grid()
        else:
            self.platte_cb.grid_remove()
            self.platte_lbl.grid_remove()
        self._halter_changed(initial=initial)

    def _halter_changed(self, initial: bool = False) -> None:
        art = self._art()
        code = self.v["halter"].get()
        if art in DREHEN:
            h = wk.halter(code)
            plates = wk.passende_platten(h) if h else list(wk.PLATTEN)
            self.platte_cb.configure(values=[EIGENES] + plates)
            if self.v["platte"].get() not in plates:
                self.v["platte"].set(plates[0] if (h and plates and not initial) else EIGENES)
        else:
            rw = wk.rundwerkzeug(code)
            if rw is not None and not initial:
                self.v["dm"].set(fmt(rw.dm))
        self._platte_changed(initial=initial)

    def _platte_changed(self, initial: bool = False) -> None:
        halter = "" if self.v["halter"].get() == EIGENES else self.v["halter"].get()
        platte = "" if self.v["platte"].get() == EIGENES else self.v["platte"].get()
        suggestion = self._suggest_name(halter, platte, self._art())
        name = self.v["name"].get().strip()
        if not initial and suggestion and (not name or name == self._auto_name):
            self.v["name"].set(suggestion)
        self._auto_name = suggestion or self._auto_name
        self._update_info(halter, platte)

    @staticmethod
    def _suggest_name(halter: str, platte: str, art: str) -> str:
        if art in DREHEN:
            return kurzname(platte)
        rw = wk.rundwerkzeug(halter)
        if rw is None:
            return ""
        return {"fraeser": "FRAESER D{:g}", "bohrer": "BOHRER D{:g}", "fase": "FASENFRAESER D{:g}"}.get(
            rw.art, "{:g}").format(rw.dm)

    def _update_info(self, halter: str, platte: str) -> None:
        parts = []
        h = wk.halter(halter)
        p = wk.parse_platte(platte)
        rw = wk.rundwerkzeug(halter)
        if h:
            parts.append("{}: {}".format(h.code, h.beschreibung))
        if p:
            parts.append("{}: {}".format(p.code, p.beschreibung))
        if rw:
            parts.append(rw.beschreibung)
        if self._art() in DREHEN and not (h and p):
            parts.append("Ohne Halter und Platte nimmt die Simulation eine übliche Vorgabe.")
        if self._art() == "bohrer" and self.v["lage"].get() == LAGEN["mitte"]:
            parts.append("Stehend in Drehmitte: bohrt mit Spindeldrehzahl S und Vorschub mm/U (Vorbohren).")
        self.info.configure(text="  ·  ".join(parts))

    # -- Ergebnis -------------------------------------------------------------

    def ok(self) -> None:
        v = self.v
        try:
            t = int(parse_num(v["t"].get()))
            d = int(parse_num(v["d"].get() or "1"))
            dm = parse_num(v["dm"].get() or "0")
            s = parse_num(v["s"].get())
            f = parse_num(v["f"].get())
            ap = parse_num(v["ap"].get() or "0")
        except ValueError:
            self.msg.configure(text="T, D, S, F, Ø und Zustellung als Zahl eintragen (Komma oder Punkt).")
            return
        if not 0 < t < 1000:
            self.msg.configure(text="T-Nummer 1 … 999.")
            return
        art = self._art()
        halter = "" if v["halter"].get() == EIGENES else v["halter"].get()
        platte = "" if v["platte"].get() == EIGENES or art not in DREHEN else v["platte"].get()
        h, p = wk.halter(halter), wk.parse_platte(platte)
        if h and p and not wk.platte_passt(h, p):
            self.msg.configure(text="Die Platte {} passt nicht in den Halter {}.".format(p.code, h.code))
            return
        lage = "" if art in DREHEN else _inv(LAGEN).get(v["lage"].get(), "radial")
        self.result = Werkzeug(t, d, v["name"].get().strip(), art, lage, dm, s, f, ap, halter, platte)
        self.destroy()
